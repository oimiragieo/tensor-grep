# Implementation Plan: HANDLER-CENSUS-W2-c

Executor: unlazy
Approved: claude-opus-5 2026-09-03 APPROVED run-w2c-001

## Objective
Append 8 handler disposition records for `backends/ast_backend.py` (2), `backends/ast_wrapper_backend.py` (3), `backends/rust_backend.py` (2), and `backends/stringzilla_backend.py` (1) to `docs/audits/2026-08-20-handler-dispositions.json` and add these four modules to `_EXPLICIT_AUDITED_MODULES` in `tests/unit/test_handler_dispositions.py`. Relabeled as W2-c per `docs/plans/HANDLER-CENSUS-W2.md` (W2-b GPU backends remain deferred/queued).

## File Map
- Modify: `tests/unit/test_handler_dispositions.py` (add 4 module strings to `_EXPLICIT_AUDITED_MODULES`)
- Modify: `docs/audits/2026-08-20-handler-dispositions.json` (append 8 records matching strict schema)
- Modify: `docs/plans/HANDLER-CENSUS-W2.md` (record W2-c progress)

## Schema Definition
Each appended ledger entry adheres strictly to:
- `module`: str (e.g. `"backends/ast_backend.py"`)
- `enclosing_symbol`: str
- `handler_index_within_symbol`: int
- `lineno`: int (advisory linenos: 662, 803, 401, 447, 481, 268, 321, 449)
- `category`: "INTENTIONAL-BOUNDARY" | "LOGGED-DEGRADE"
- `reason`: str (non-empty, distinct, informative)
- `evidence`: str (non-empty, distinct, cited)
- `hardened_in`: null (pure census disposition; no handler modification)

## Tasks

### Task 1: RED — extend audited set in test_handler_dispositions.py
- Add `"backends/ast_backend.py"`, `"backends/ast_wrapper_backend.py"`, `"backends/rust_backend.py"`, `"backends/stringzilla_backend.py"` to `_EXPLICIT_AUDITED_MODULES`.
- Run `pytest tests/unit/test_handler_dispositions.py::test_ledger_completeness_scoped_to_audited_modules` to verify RED failure showing exactly the 8 missing handler identity triples:
  1. `("backends/ast_backend.py", "_get_parser", 0)`
  2. `("backends/ast_backend.py", "search", 0)`
  3. `("backends/ast_wrapper_backend.py", "search_project", 0)`
  4. `("backends/ast_wrapper_backend.py", "search_many", 0)`
  5. `("backends/ast_wrapper_backend.py", "search", 0)`
  6. `("backends/rust_backend.py", "search", 0)`
  7. `("backends/rust_backend.py", "search", 1)`
  8. `("backends/stringzilla_backend.py", "search", 0)`

### Task 2: GREEN — append 8 disposition records to docs/audits/2026-08-20-handler-dispositions.json
- Append the 8 records with exact identity keys and full schema compliance:
  1. `backends/ast_backend.py::_get_parser[0]`: `INTENTIONAL-BOUNDARY`
  2. `backends/ast_backend.py::search[0]`: `INTENTIONAL-BOUNDARY`
  3. `backends/ast_wrapper_backend.py::search_project[0]`: `INTENTIONAL-BOUNDARY`
  4. `backends/ast_wrapper_backend.py::search_many[0]`: `INTENTIONAL-BOUNDARY`
  5. `backends/ast_wrapper_backend.py::search[0]`: `INTENTIONAL-BOUNDARY`
  6. `backends/rust_backend.py::search[0]`: `LOGGED-DEGRADE`
  7. `backends/rust_backend.py::search[1]`: `INTENTIONAL-BOUNDARY`
  8. `backends/stringzilla_backend.py::search[0]`: `INTENTIONAL-BOUNDARY`
- Run `pytest tests/unit/test_handler_dispositions.py` to confirm GREEN (all 11 tests pass).

### Task 3: Regression, Governance & Ratchet Verification
- Run `pytest tests/unit/test_silent_failure_hardening.py` to confirm no ceiling violations.
- Run `pytest tests/unit/test_governance_doc_size_ratchet.py` to verify documentation budgets.
- Run `ruff check` and `mypy src/tensor_grep`.
