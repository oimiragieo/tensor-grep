# Backlog Top Slice Implementation Plan: Markdown Hygiene, Governance Doc Size Ratchet, and Symbol Priority (Rev 2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Execute the top approved slice from `docs/BACKLOG.md` and `docs/TASK_BOARD.md` by clearing repo-wide markdown formatting debt (`HYGIENE-FORMAT`), introducing a governance document size budget ratchet (`GOVERNANCE-DOC-SIZE-RATCHET`), sanitizing empty/whitespace confidence downgrade reasons (`CONFIDENCE-HYGIENE`), and resolving the `_add` lexical trap in production pipelines (`SYMBOL-PRIORITY-PREFERENCE`).

**Architecture:** 
1. Format `AGENTS.md`, `docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md`, and `docs/plans/2026-09-03-backlog-top-slice-execution.md` with `ruff format --preview` to achieve 100% clean formatting repo-wide.
2. Build `scripts/check_governance_doc_size.py` and `tests/unit/test_governance_doc_size_ratchet.py` to enforce a hard ratchet on top-level governance documents (`AGENTS.md`, `docs/BACKLOG.md`, `docs/TASK_BOARD.md`, `CLAUDE.md`) with verified live baselines and timeout protection.
3. Enhance `src/tensor_grep/cli/agent_capsule_confidence.py` to sanitize and filter empty/whitespace downgrade reasons, tested in canonical `tests/unit/test_confidence_invariant_is_enforced.py`.
4. Implement `_prefer_public_implementation_over_private_helper` in `src/tensor_grep/cli/agent_capsule_targets.py` with query-term relevance matching and a confidence floor (`alt_confidence >= 0.7`). Wire it into both production capsule pipelines (`agent_capsule_builder.py` and `agent_capsule_confidence.py`), backed by unit tests and end-to-end integration tests in `tests/unit/test_agent_capsule_hardcases.py`.

**Tech Stack:** Python 3.12, Typer, pytest, ruff, AST visitor, Git.

---

### Task 1: Complete Markdown Formatting Hygiene (`HYGIENE-FORMAT`)

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md`
- Modify: `docs/plans/2026-09-03-backlog-top-slice-execution.md`
- Test: Repo-wide `ruff format --preview --check .`

**Step 1: Run format check to identify exact failures**
Run: `uv run --no-sync ruff format --preview --check .`
Expected: 3 files identified.

**Step 2: Apply formatting**
Run: `uv run --no-sync ruff format --preview AGENTS.md docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md docs/plans/2026-09-03-backlog-top-slice-execution.md`

**Step 3: Verify formatting passes repo-wide**
Run: `uv run --no-sync ruff format --preview --check .`
Expected: `1000 files already formatted` (clean exit 0).

**Step 4: Commit**
```bash
git add -- AGENTS.md docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md docs/plans/2026-09-03-backlog-top-slice-execution.md
git commit -m "style: format markdown files to satisfy ruff format --preview"
```

---

### Task 2: Governance Document Size Budget Ratchet (`GOVERNANCE-DOC-SIZE-RATCHET`)

**Files:**
- Create: `scripts/check_governance_doc_size.py`
- Create: `tests/unit/test_governance_doc_size_ratchet.py`
- Test: `tests/unit/test_governance_doc_size_ratchet.py`

**Step 1: Write the failing test**
Create `tests/unit/test_governance_doc_size_ratchet.py`:
```python
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_governance_doc_size_ratchet() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_governance_doc_size.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Governance doc size budget exceeded:\n{result.stdout}\n{result.stderr}"
    )
```

**Step 2: Run test to verify it fails**
Run: `uv run --no-sync pytest tests/unit/test_governance_doc_size_ratchet.py -v`
Expected: FAIL (script does not exist yet).

**Step 3: Write minimal implementation**
Create `scripts/check_governance_doc_size.py`:
```python
"""Governance document size budget ratchet.

Prevents silent unbounded expansion of central governance files.
Sizes are measured in UTF-8 bytes and line count.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pinned maximum allowable byte sizes and line counts (calibrated against live baselines)
# AGENTS.md baseline: 378 KB, 3,895 lines
# docs/BACKLOG.md baseline: 354 KB, 3,728 lines
# docs/TASK_BOARD.md baseline: 62 KB, 523 lines
# CLAUDE.md baseline: 26 KB, 206 lines
PINNED_BUDGETS: dict[str, dict[str, int]] = {
    "AGENTS.md": {"max_bytes": 420_000, "max_lines": 4_100},
    "docs/BACKLOG.md": {"max_bytes": 400_000, "max_lines": 4_000},
    "docs/TASK_BOARD.md": {"max_bytes": 80_000, "max_lines": 700},
    "CLAUDE.md": {"max_bytes": 35_000, "max_lines": 350},
}


def main() -> int:
    violations: list[str] = []
    for rel_path, budget in PINNED_BUDGETS.items():
        doc_path = REPO_ROOT / rel_path
        if not doc_path.exists():
            violations.append(f"Missing governance document: {rel_path}")
            continue
        content = doc_path.read_bytes()
        actual_bytes = len(content)
        actual_lines = len(content.splitlines())
        max_bytes = budget["max_bytes"]
        max_lines = budget["max_lines"]
        if actual_bytes > max_bytes:
            violations.append(f"{rel_path}: size {actual_bytes} bytes exceeds ceiling {max_bytes}")
        if actual_lines > max_lines:
            violations.append(f"{rel_path}: lines {actual_lines} exceeds ceiling {max_lines}")

    if violations:
        print("Governance doc size budget violations:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("Governance doc size budget OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 4: Run test to verify it passes**
Run: `uv run --no-sync pytest tests/unit/test_governance_doc_size_ratchet.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add -- scripts/check_governance_doc_size.py tests/unit/test_governance_doc_size_ratchet.py
git commit -m "feat(governance): enforce size ratchet on central governance documents"
```

---

### Task 3: Confidence Downgrade Reasons Hygiene (`CONFIDENCE-HYGIENE`)

**Files:**
- Modify: `src/tensor_grep/cli/agent_capsule_confidence.py:228-250`
- Test: `tests/unit/test_confidence_invariant_is_enforced.py`

**Step 1: Write the failing test**
In `tests/unit/test_confidence_invariant_is_enforced.py`, add:
```python
def test_empty_or_whitespace_downgrade_reasons_do_not_lower_confidence() -> None:
    from tensor_grep.cli.agent_capsule_confidence import _confidence

    payload = {"edit_plan_seed": {"confidence": {"overall": 1.0}}}
    res = _confidence(
        payload, snippets=[{"file": "foo.py"}], downgrade_reasons=["", "   "], consistency={}
    )
    assert res["overall"] == 1.0
    assert res["downgrade_reasons"] == []
```

**Step 2: Run test to verify it fails**
Run: `uv run --no-sync pytest tests/unit/test_confidence_invariant_is_enforced.py -k test_empty_or_whitespace -v`
Expected: FAIL (empty strings currently trigger `deduped_reasons` branch and drop confidence to 0.99).

**Step 3: Write minimal implementation**
In `src/tensor_grep/cli/agent_capsule_confidence.py`, update `_confidence`:
```python
valid_reasons = [r.strip() for r in downgrade_reasons if isinstance(r, str) and r.strip()]
deduped_reasons = list(dict.fromkeys(valid_reasons))
```

**Step 4: Run test to verify it passes**
Run: `uv run --no-sync pytest tests/unit/test_confidence_invariant_is_enforced.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add -- src/tensor_grep/cli/agent_capsule_confidence.py tests/unit/test_confidence_invariant_is_enforced.py
git commit -m "fix(confidence): filter empty or whitespace strings from downgrade reasons"
```

---

### Task 4: Fix `_add` Lexical Trap via Public Symbol Preference & Pipeline Wiring (`SYMBOL-PRIORITY-PREFERENCE`)

**Files:**
- Modify: `src/tensor_grep/cli/agent_capsule_targets.py`
- Modify: `src/tensor_grep/cli/agent_capsule_builder.py:346-348`
- Modify: `src/tensor_grep/cli/agent_capsule_confidence.py:330-332`
- Create: `tests/unit/test_agent_capsule_symbol_priority.py`
- Modify: `tests/unit/test_agent_capsule_hardcases.py`

**Step 1: Write the failing tests**
Create `tests/unit/test_agent_capsule_symbol_priority.py`:
```python
from tensor_grep.cli.agent_capsule_targets import _prefer_public_implementation_over_private_helper


def test_prefer_public_implementation_over_private_helper() -> None:
    primary = {
        "file": "src/tensor_grep/cli/repo_map_lang_python.py",
        "symbol": "_add",
        "kind": "function",
        "confidence": 1.0,
    }
    alternatives = [
        {
            "file": "src/tensor_grep/core/retrieval.py",
            "symbol": "replace_with_retry",
            "kind": "function",
            "confidence": 0.85,
        }
    ]
    query = "add retry with tests"
    new_primary, new_alts = _prefer_public_implementation_over_private_helper(
        query, primary, alternatives
    )
    assert new_primary["symbol"] == "replace_with_retry"
    assert new_alts[0]["symbol"] == "_add"


def test_prefer_public_implementation_ignores_low_confidence_unrelated_alternatives() -> None:
    primary = {
        "file": "src/tensor_grep/cli/repo_map_lang_python.py",
        "symbol": "_add",
        "kind": "function",
        "confidence": 1.0,
    }
    # Low confidence or unrelated alternatives must NOT hijack the primary
    alternatives = [
        {
            "file": "src/tensor_grep/cli/irrelevant.py",
            "symbol": "unrelated_tool",
            "kind": "function",
            "confidence": 0.3,
        }
    ]
    query = "add retry with tests"
    new_primary, new_alts = _prefer_public_implementation_over_private_helper(
        query, primary, alternatives
    )
    assert new_primary["symbol"] == "_add"
```

**Step 2: Run test to verify it fails**
Run: `uv run --no-sync pytest tests/unit/test_agent_capsule_symbol_priority.py -v`
Expected: FAIL (`_prefer_public_implementation_over_private_helper` not defined).

**Step 3: Write minimal implementation and wire pipelines**
1. In `src/tensor_grep/cli/agent_capsule_targets.py`:
```python
def _prefer_public_implementation_over_private_helper(
    query: str,
    primary_target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Promote a genuine public implementation over an unrequested private helper primary (e.g. `_add`).

    When the query does NOT explicitly search for a `_`-prefixed identifier, but BM25/lexical
    scoring selects a private helper (`_add`) that happens to match a common verb ("add"),
    and viable public non-test function alternatives exist (confidence >= 0.7 with term overlap),
    prefer the public implementation.
    """
    symbol = str(primary_target.get("symbol") or "")
    if not (symbol.startswith("_") and not symbol.startswith("__")):
        return primary_target, alternatives

    query_terms = set(repo_map._query_terms(query))
    if any(term.startswith("_") for term in query_terms):
        return primary_target, alternatives

    best_index = -1
    best_confidence = -1.0
    for index, alternative in enumerate(alternatives):
        alt_symbol = str(alternative.get("symbol") or "")
        if not alt_symbol or alt_symbol.startswith("_"):
            continue
        alt_kind = str(alternative.get("kind") or "")
        if alt_kind not in {"function", "method", "class"}:
            continue
        alt_confidence = _numeric_confidence(alternative.get("confidence"), 0.0)
        if alt_confidence < 0.7:
            continue
        alt_terms = set(split_terms(alt_symbol))
        if not (alt_terms & query_terms):
            continue
        if alt_confidence > best_confidence:
            best_confidence = alt_confidence
            best_index = index

    if best_index < 0:
        return primary_target, alternatives

    public_impl = alternatives[best_index]
    demoted = [*alternatives[:best_index], *alternatives[best_index + 1 :]]
    demoted.insert(0, primary_target)
    return public_impl, demoted
```

2. Wire into `src/tensor_grep/cli/agent_capsule_builder.py` around line 347:
```python
target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
target, alternatives = _prefer_implementation_over_cli_dispatcher_helper(target, alternatives)
target, alternatives = _prefer_public_implementation_over_private_helper(
    query, target, alternatives
)
```

3. Wire into `src/tensor_grep/cli/agent_capsule_confidence.py` around line 331:
```python
target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
target, alternatives = _prefer_implementation_over_cli_dispatcher_helper(target, alternatives)
target, alternatives = _prefer_public_implementation_over_private_helper(
    query, target, alternatives
)
```

**Step 4: Run tests to verify they pass**
Run: `uv run --no-sync pytest tests/unit/test_agent_capsule_symbol_priority.py tests/unit/test_agent_capsule_hardcases.py -v`
Expected: PASS.

**Step 5: Commit**
```bash
git add -- src/tensor_grep/cli/agent_capsule_targets.py src/tensor_grep/cli/agent_capsule_builder.py src/tensor_grep/cli/agent_capsule_confidence.py tests/unit/test_agent_capsule_symbol_priority.py tests/unit/test_agent_capsule_hardcases.py
git commit -m "fix(agent): wire public implementation preference over unrequested private helpers"
```
