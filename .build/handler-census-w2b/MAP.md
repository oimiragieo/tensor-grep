# MAP — HANDLER-CENSUS-W2B: AST, Rust, and StringZilla Backend Disposition Ledger

## Destination
Complete machine-enforced audit and disposition ledger rows for all 8 broad `except Exception` handlers in `backends/ast_backend.py` (2), `backends/ast_wrapper_backend.py` (3), `backends/rust_backend.py` (2), and `backends/stringzilla_backend.py` (1), extending `_EXPLICIT_AUDITED_MODULES` in `tests/unit/test_handler_dispositions.py` so these four backend engines are strictly covered by the ledger completeness gate.

## Open questions

<!-- zero open questions: all answered -->

## Answers

### Q1: What modules and handlers are in scope for HANDLER-CENSUS-W2B?
**Answer:** Exactly 8 handlers across four backend files:
1. `backends/ast_backend.py`: `_get_parser` (idx 0, lineno 662), `search` (idx 0, lineno 803)
2. `backends/ast_wrapper_backend.py`: `search_project` (idx 0, lineno 401), `search_many` (idx 0, lineno 447), `search` (idx 0, lineno 481)
3. `backends/rust_backend.py`: `search` (idx 0, lineno 268), `search` (idx 1, lineno 321)
4. `backends/stringzilla_backend.py`: `search` (idx 0, lineno 449)
**Why:** These 8 handlers represent the closed world of broad handlers in non-GPU backend engines in `src/tensor_grep/backends/`. Completing them extends machine-gated ledger coverage from 2 modules to 6 modules.
**Check:** python -c "import sys; sys.path.insert(0, 'tests/unit'); from test_handler_dispositions import _real_handlers_for_module; assert sum(len(_real_handlers_for_module(m)) for m in ['backends/ast_backend.py', 'backends/ast_wrapper_backend.py', 'backends/rust_backend.py', 'backends/stringzilla_backend.py']) == 8"
**Judged by:** run it
**Reference:** docs/audits/2026-08-30-handler-census-w2-backends.json

### Q2: What are the dispositions and categories for each of the 8 handlers?
**Answer:**
1. `ast_backend.py::_get_parser[0]`: `INTENTIONAL-BOUNDARY` (re-raises `RuntimeError` on tree-sitter grammar load failure with context)
2. `ast_backend.py::search[0]`: `INTENTIONAL-BOUNDARY` (re-raises `BackendExecutionError` for AST dependency/query failures)
3. `ast_wrapper_backend.py::search_project[0]`: `INTENTIONAL-BOUNDARY` (re-raises `BackendExecutionError(f"AstGrepWrapperBackend failed: {e}")`)
4. `ast_wrapper_backend.py::search_many[0]`: `INTENTIONAL-BOUNDARY` (re-raises `BackendExecutionError(f"AstGrepWrapperBackend failed: {e}")`)
5. `ast_wrapper_backend.py::search[0]`: `INTENTIONAL-BOUNDARY` (re-raises `BackendExecutionError(f"AstGrepWrapperBackend failed: {e}")`)
6. `rust_backend.py::search[0]`: `LOGGED-DEGRADE` (passthrough failure records bridge_fallback_reason and falls back to python regex; re-raises InvalidRegexError or BackendExecutionError on PCRE2)
7. `rust_backend.py::search[1]`: `INTENTIONAL-BOUNDARY` (re-raises InvalidRegexError or BackendExecutionError on Rust engine search failure)
8. `stringzilla_backend.py::search[0]`: `INTENTIONAL-BOUNDARY` (re-raises `BackendExecutionError` on StringZilla search failure)
**Why:** Every handler has been inspected in context. 7 of 8 are fail-closed typed re-raises (`INTENTIONAL-BOUNDARY`), and 1 is a tracked bridge fallback with visible `fallback_reason` recording (`LOGGED-DEGRADE`). None silently swallow errors.
**Check:** uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
**Judged by:** run it
**Reference:** docs/audits/2026-08-20-handler-dispositions.json

### Q3: How is the completeness ratchet updated without breaking preexisting gates?
**Answer:** In `tests/unit/test_handler_dispositions.py`, append `"backends/ast_backend.py"`, `"backends/ast_wrapper_backend.py"`, `"backends/rust_backend.py"`, `"backends/stringzilla_backend.py"` to `_EXPLICIT_AUDITED_MODULES`. Do not alter `TOTAL_BROAD_HANDLERS_CEILING` because no broad handlers are removed or added.
**Why:** The explicit set tells the completeness test to demand disposition rows for these modules. Since no handler was eliminated, the population ceiling in `test_silent_failure_hardening.py` remains unchanged per Rule A137.
**Check:** uv run --no-sync python -m pytest tests/unit/test_silent_failure_hardening.py -q
**Judged by:** run it
**Reference:** tests/unit/test_handler_dispositions.py
