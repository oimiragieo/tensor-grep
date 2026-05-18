# Dogfood World-Class Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tg dogfood` machine-readable about what a PASS does not prove, including target-selection accuracy.

**Architecture:** Extend the existing deterministic `world_class_readiness.limitations` payload instead of adding a separate roadmap array. Keep the report non-blocking, static, and additive so CI and agents can parse it without changing release gate semantics.

**Tech Stack:** Python Typer CLI, `tensor_grep.cli.dogfood`, pytest, Ruff, mypy, validator-backed docs.

---

## Research And Review

- Exa research anchors:
  - ripgrep docs/manpage: JSON is JSON Lines, config override flags include inverse forms, and rg remains the raw automation baseline.
  - ast-grep CLI docs: full parity would require more than `tg run`, including scan/test/new surfaces and options such as selector, strictness, stdin, globs, and update workflows.
  - Cursor and Sourcegraph agentic context docs: agent code intelligence depends on tool chaining, context limits, evidence, and iterative retrieval.
  - NVIDIA CUDA guidance: GPU promotion needs profiling, transfer accounting, realistic workload proof, and speed wins beyond device discovery.
- Thinktank review:
  - Gemini plan-mode read-only review rejected a separate `next_pr_slices` array because it would duplicate planning priorities inside runtime JSON.
  - Accepted direction: add the missing `agent_target_selection_metrics` surface to the existing `limitations` array.

## Task 1: Add Target-Selection Limitation To Dogfood Contract

**Files:**
- Modify: `tests/unit/test_dogfood_cli.py`
- Modify: `src/tensor_grep/cli/dogfood.py`
- Modify: `docs/CONTRACTS.md`
- Modify: `tests/unit/test_public_docs_governance.py`

- [x] **Step 1: Write the failing dogfood contract test**

Add `agent_target_selection_metrics` to the expected `world_class_readiness.limitations` surfaces in `tests/unit/test_dogfood_cli.py`.

Run:

```powershell
uv run pytest tests/unit/test_dogfood_cli.py::test_dogfood_command_wraps_agent_readiness_report -q
```

Expected before implementation: FAIL because the limitation surface is missing.

- [x] **Step 2: Add the dogfood payload surface**

Add this object to `src/tensor_grep/cli/dogfood.py` under `world_class_readiness.limitations`:

```python
{
    "surface": "agent_target_selection_metrics",
    "status": "missing_enterprise_accuracy_gate",
    "required_evidence": (
        "accepted target-selection metrics such as top-k hit rate, MRR, "
        "false-primary rate, validation-command precision, and ambiguity "
        "handling on mixed-language and noisy-repo hardcases"
    ),
}
```

- [x] **Step 3: Document the additive contract**

Update `docs/CONTRACTS.md` and its governance test to require `agent_target_selection_metrics` in the dogfood contract prose.

Run:

```powershell
uv run pytest tests/unit/test_public_docs_governance.py::test_contracts_should_record_windows_shell_and_ordering_limits -q
```

Expected after implementation: PASS.

- [x] **Step 4: Run targeted verification**

Run:

```powershell
uv run pytest tests/unit/test_dogfood_cli.py::test_dogfood_command_wraps_agent_readiness_report tests/unit/test_public_docs_governance.py::test_contracts_should_record_windows_shell_and_ordering_limits -q
```

Expected: PASS.

## Task 2: Preserve Release Workflow Ledger

**Files:**
- Modify: `AGENTS.md`
- Modify: `SKILL.md`
- Modify: `docs/SESSION_HANDOFF.md`

- [x] **Step 1: Record the implementation slice**

Record the Exa anchors, thinktank decision, subagent status, validation evidence, and pending PR/main CI status in all three handoff files.

- [x] **Step 2: Verify formatting and tests**

Run the normal local gates before push:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
C:\Users\oimir\.cargo\bin\cargo.exe test --manifest-path rust_core\Cargo.toml
C:\Users\oimir\.cargo\bin\cargo.exe fmt --manifest-path rust_core\Cargo.toml --check
git diff --check
```

Expected: all checks pass, with only Git line-ending warnings allowed from `git diff --check`.
