# tensor-grep v1.7.0 Post-Release Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for independent implementation slices or `superpowers:executing-plans` for inline execution. Steps use checkbox syntax for tracking.

**Goal:** Prove the shipped `v1.7.0` release works across CLI, MCP, edit, and GPU discovery surfaces, then document only evidence-backed changes.

**Architecture:** This is an audit-first plan. It does not change runtime architecture unless a concrete failing check requires a TDD fix.

**Tech Stack:** Python, Rust/PyO3, FastMCP, GitHub Actions, `uv`, pytest, ruff, mypy, benchmark scripts.

---

## Task 1: Workspace And Release Baseline

**Files:**
- Read: `README.md`
- Read: `docs/PAPER.md`
- Read: `AGENTS.md`
- Modify: `docs/plans/requirements-tensor-grep-2026-04-30_07-53-27.md`
- Modify: `docs/plans/design-tensor-grep-2026-04-30_07-53-27.md`
- Modify: `docs/plans/tasks-tensor-grep-2026-04-30_07-53-27.md`

- [x] **Step 1: Preserve conflicting untracked planning docs**

Run:

```powershell
Copy-Item docs/plans/*2026-04-29_01-45-34.md C:/dev/projects/tensor-grep-local-backups/v1.7.0-plan-docs-20260430-075041/
```

Expected: local-only copies preserved before fast-forward.

- [x] **Step 2: Sync local main**

Run:

```powershell
git pull --ff-only origin main
```

Expected: local `main` fast-forwards to release commit `f1f1354`.

- [x] **Step 3: Verify workspace state**

Run:

```powershell
git status --short --branch
```

Expected: branch reports `main...origin/main`; only intentional plan docs and accepted audit fixes may be modified.

## Task 2: PyPI Release Smoke

**Files:**
- No repo file edits expected.

- [x] **Step 1: Verify PyPI CLI version**

Run:

```powershell
uvx --from tensor-grep==1.7.0 tg --version
```

Expected: includes `tensor-grep 1.7.0`.

- [x] **Step 2: Verify PyPI help loads**

Run:

```powershell
uvx --from tensor-grep==1.7.0 tg --help
```

Expected: exits 0 and prints the top-level `tg` help.

- [x] **Step 3: Verify PyPI MCP capability registry**

Run outside the repo to avoid import shadowing:

```powershell
uv run --with tensor-grep==1.7.0 --with fastmcp python -c "import json; from tensor_grep.cli.mcp_server import tg_mcp_capabilities; data=json.loads(tg_mcp_capabilities()); print(data['contract_version'], data['routing_backend'], len(data['tools']), 'tg_mcp_capabilities' in data['tools'])"
```

Expected: `1 MCPRuntime 41 True`.

Actual corrected probe shape:

```powershell
uv run --with tensor-grep==1.7.0 --with fastmcp python -c "import json; from tensor_grep.cli.mcp_server import tg_mcp_capabilities; data=json.loads(tg_mcp_capabilities()); names=[item['name'] for item in data['tools']]; print(data['version'], data['routing_backend'], len(data['tools']), 'tg_mcp_capabilities' in names)"
```

Expected: `1 MCPRuntime 41 True`.

## Task 3: Local Operational Audit

**Files:**
- Read: `src/tensor_grep/cli/mcp_server.py`
- Read: `src/tensor_grep/cli/main.py`
- Read: `tests/unit/test_harness_api_docs.py`
- Read: `tests/unit/test_harness_cookbook.py`
- Optional modify only if a concrete failure is found.

- [x] **Step 1: Verify local CLI version**

Run:

```powershell
uv run tg --version
```

Expected: local source reports `tensor-grep 1.7.0`.

- [x] **Step 2: Verify local MCP capabilities**

Run:

```powershell
uv run python -c "import json; from tensor_grep.cli.mcp_server import tg_mcp_capabilities; data=json.loads(tg_mcp_capabilities()); print(data['routing_backend'], len(data['tools']), data['tools']['tg_mcp_capabilities']['mode'])"
```

Expected: prints `MCPRuntime 41 python-local`.

Actual corrected probe shape:

```powershell
uv run python -c "import json; from tensor_grep.cli.mcp_server import tg_mcp_capabilities; data=json.loads(tg_mcp_capabilities()); tool={item['name']: item for item in data['tools']}; print(data['routing_backend'], len(data['tools']), tool['tg_mcp_capabilities']['mode'])"
```

Expected: `MCPRuntime 41 python-local`.

- [x] **Step 3: Verify local GPU/device discovery does not crash**

Run:

```powershell
uv run tg doctor --json
uv run tg devices --json
```

Expected: exit 0 or documented infrastructure `SKIP`/unavailable state. If command shape differs, inspect `tg --help` and use the documented equivalent.

- [x] **Step 4: Verify docs/MCP contracts**

Run:

```powershell
uv run pytest tests/unit/test_harness_api_docs.py tests/unit/test_harness_cookbook.py -q
```

Expected: all selected tests pass.

## Task 4: Benchmarks And Docs Decision

**Files:**
- Read: `docs/benchmarks.md`
- Read: `docs/gpu_crossover.md`
- Read: `docs/PAPER.md`
- Read: `README.md`
- Optional artifact output under `artifacts/`
- Optional docs update only if benchmark results change the accepted story.

- [x] **Step 1: Run GPU benchmark if dependencies allow**

Run:

```powershell
uv run python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks_post_v170_audit.json
```

Expected: pass with measured rows or top-level `SKIP`. A `SKIP` is acceptable infrastructure evidence.

Actual: `artifacts/bench_run_gpu_benchmarks_post_v170_audit.json` reported no GPU auto-routing recommendation. RTX 4070 completed small sidecar rows slower than `rg`; RTX 5070 remained unsupported by the current PyTorch/CUDA sidecar stack.

Additional native crossover audit:

```powershell
uv run python benchmarks/run_gpu_native_benchmarks.py --output artifacts/bench_run_gpu_native_benchmarks_post_v170_audit.json
```

Actual: exited non-zero because the artifact has `passed = false`, with no crossover. RTX 4070 completed 10MB/100MB slower than `rg` and timed out at 500MB/1GB.

- [x] **Step 2: Run AST rewrite benchmark if edit docs need refresh**

Run:

```powershell
uv run python benchmarks/run_ast_rewrite_benchmarks.py --output artifacts/bench_ast_rewrite_post_v170_audit.json
```

Expected: pass or produce a concrete benchmark failure to debug.

Actual: `artifacts/bench_ast_rewrite_post_v170_audit.json` passed with `tg apply 0.464s`, `sg apply 0.537s`, `tg/sg = 0.865x`.

- [x] **Step 3: Decide docs update**

If artifacts match the existing accepted story, do not change README or `docs/PAPER.md`. If they differ, update the relevant docs and run validator-backed tests.

Actual: README, `docs/benchmarks.md`, `docs/gpu_crossover.md`, `docs/PAPER.md`, and CHANGELOG were updated with the post-release audit and release-validator drift.

## Task 5: Release Gates For Any Code Change

**Files:**
- Modify only files required by a concrete failing test.

- [x] **Step 1: Write failing test before implementation**

Expected: the test fails for the observed bug before code changes.

Actual: `test_should_fail_when_uv_lock_editable_version_mismatches_expected` failed before the validator change.

- [x] **Step 2: Implement smallest fix**

Expected: only the failure-specific code path changes.

Actual: `scripts/validate_release_version_parity.py` now checks the editable `uv.lock` version.

- [x] **Step 3: Run local gates**

Run:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

Expected: all pass before any push.

Actual:

- `uv run ruff check .` passed.
- `uv run ruff format --check --preview .` passed after preview-formatting `tests/unit/test_repo_retrieval_benchmark_scripts.py`.
- `uv run mypy src/tensor_grep` passed.
- `uv run pytest -q` passed with `1714 passed, 21 skipped`.

## Rollback Plan

- Revert only the specific commit or docs patch created by this audit.
- Do not revert unrelated local or user changes.
- If a benchmark artifact shows regression, reject the candidate and record the rejected result in `docs/PAPER.md`.

## Definition Of Done

- Release smoke evidence recorded in final report.
- Local CLI/MCP/GPU discovery checks run or blockers documented.
- Docs changed only when fresh artifacts require it.
- Any code-bearing fix has TDD evidence, full gates, and benchmark proof when hot-path related.
