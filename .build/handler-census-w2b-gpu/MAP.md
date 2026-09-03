# MAP — HANDLER-CENSUS-W2B-GPU: CuDF, Torch, and CyBERT GPU Backend Disposition Ledger

## Destination
Complete machine-enforced audit and disposition ledger rows for all 21 broad `except Exception` handlers in `backends/cudf_backend.py` (7), `backends/torch_backend.py` (5), and `backends/cybert_backend.py` (9, following in-slice narrowing of deobfuscate_payload), extending `_EXPLICIT_AUDITED_MODULES` in `tests/unit/test_handler_dispositions.py` so the entire `backends/` directory (all 46 backend broad handlers across all 9 modules) is 100% covered by the ledger completeness gate.

## Open questions

<!-- zero open questions: all answered -->

## Answers

### Q1: What modules and handlers are in scope for HANDLER-CENSUS-W2B-GPU?
**Answer:** Exactly 21 broad handlers across three GPU backend files (after narrowing cybert_backend.py deobfuscate_payload to ValueError, binascii.Error):
1. `backends/cudf_backend.py`: `_process_chunk_on_device` (idx 0, lineno 71), `_process_chunk_on_device` (idx 1, lineno 77), `_device_context` (idx 0, lineno 150), `is_available` (idx 0, lineno 234), `search` (idx 0, lineno 337), `_search_uncapped` (idx 0, lineno 439), `_search_uncapped` (idx 1, lineno 585)
2. `backends/torch_backend.py`: `is_available` (idx 0, lineno 103), `is_available` (idx 1, lineno 118), `is_available` (idx 2, lineno 122), `search` (idx 0, lineno 202), `search` (idx 1, lineno 312)
3. `backends/cybert_backend.py`: `_triton_model_ready` (idx 0, lineno 128), `tokenize` (idx 0, lineno 189), `tokenize` (idx 1, lineno 203), `is_available` (idx 0, lineno 224), `search` (idx 0, lineno 260), `_classify_impl` (idx 0, lineno 333), `_classify_impl` (idx 1, lineno 355), `_classify_impl` (idx 2, lineno 378), `_classify_impl` (idx 3, lineno 386)
**Why:** These 21 handlers represent the remaining closed world of broad handlers in `src/tensor_grep/backends/` following the in-slice narrowing of deobfuscate_payload. Completing them brings machine-gated backend ledger coverage to 9/9 modules and 46/46 broad handlers.
**Check:** python -c "import sys; sys.path.insert(0, 'tests/unit'); from test_handler_dispositions import _real_handlers_for_module; assert sum(len(_real_handlers_for_module(m)) for m in ['backends/cudf_backend.py', 'backends/torch_backend.py', 'backends/cybert_backend.py']) == 21; print('21 handlers in scope')"
**Judged by:** run it
**Reference:** docs/audits/2026-08-30-handler-census-w2-backends.json

### Q2: What are the dispositions and categories for each of the 21 handlers?
**Answer:**
- `cudf_backend.py` (7):
  - `_process_chunk_on_device[0]`: `LOGGED-DEGRADE` (rmm reinitialize device mapping fallback with debug log)
  - `_process_chunk_on_device[1]`: `LOGGED-DEGRADE` (rmm default reinitialize failure warning before allocation)
  - `_device_context[0]`: `LOGGED-DEGRADE` (nullcontext fallback on compute capability check failure with debug log)
  - `is_available[0]`: `INTENTIONAL-BOUNDARY` (GPU capability probe fails closed)
  - `search[0]`: `INTENTIONAL-BOUNDARY` (re-raises BackendExecutionError for uncaught engine failures)
  - `_search_uncapped[0]`: `LOGGED-DEGRADE` (logs warning on zero-copy bridge failure, falls back to cudf.read_text)
  - `_search_uncapped[1]`: `LOGGED-DEGRADE` (logs warning on chunked PyArrow failure, falls back to process pool)
- `torch_backend.py` (5):
  - `is_available[0]`: `INTENTIONAL-BOUNDARY` (torch/cuda availability probe fails closed)
  - `is_available[1]`: `INTENTIONAL-BOUNDARY` (device detector probe fallback to get_device_ids)
  - `is_available[2]`: `INTENTIONAL-BOUNDARY` (device detector final probe fails closed)
  - `search[0]`: `INTENTIONAL-BOUNDARY` (device detector enumeration fallback, validates concrete IDs or raises BackendExecutionError)
  - `search[1]`: `INTENTIONAL-BOUNDARY` (re-raises BackendExecutionError on torch compute failure)
- `cybert_backend.py` (9):
  - `_triton_model_ready[0]`: `INTENTIONAL-BOUNDARY` (triton readiness probe fails closed)
  - `tokenize[0]`: `LOGGED-DEGRADE` (logs warning on cudf tokenization failure, falls back to transformers tokenizer)
  - `tokenize[1]`: `LOGGED-DEGRADE` (logs debug on transformers failure, falls back to basic tokenizer)
  - `is_available[0]`: `INTENTIONAL-BOUNDARY` (triton client availability probe fails closed)
  - `search[0]`: `INTENTIONAL-BOUNDARY` (re-raises BackendExecutionError on classification failure)
  - `_classify_impl[0]`: `LOGGED-DEGRADE` (triton client connection failure returns heuristic classify with fallback_reason)
  - `_classify_impl[1]`: `LOGGED-DEGRADE` (tokenization failure returns heuristic classify with fallback_reason or raises RuntimeError)
  - `_classify_impl[2]`: `LOGGED-DEGRADE` (traced inference failure logs debug and retries infer directly)
  - `_classify_impl[3]`: `LOGGED-DEGRADE` (inference failure returns heuristic classify with fallback_reason or raises RuntimeError)
**Why:** Every handler has been inspected in source context. Dispositions accurately record failure modes with proper logging disclosures (11 LOGGED-DEGRADE, 10 INTENTIONAL-BOUNDARY, 0 SILENT-SWALLOW).
**Check:** uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
**Judged by:** run it
**Reference:** docs/audits/2026-08-20-handler-dispositions.json

### Q3: How is the completeness ratchet updated without breaking preexisting gates?
**Answer:** In `tests/unit/test_handler_dispositions.py`, append `"backends/cudf_backend.py"`, `"backends/torch_backend.py"`, `"backends/cybert_backend.py"` to `_EXPLICIT_AUDITED_MODULES`. In `tests/unit/test_silent_failure_hardening.py`, decrement `TOTAL_BROAD_HANDLERS_CEILING` from 267 to 266 per Rule A137 reflecting the removal of deobfuscate_payload from the broad handler set.
**Why:** Machine enforcement verifies that all broad handlers in audited modules have matching records, and the ceiling ratchets down by exactly the one narrowed handler.
**Check:** uv run --no-sync python -m pytest tests/unit/test_silent_failure_hardening.py -q
**Judged by:** run it
**Reference:** tests/unit/test_handler_dispositions.py
