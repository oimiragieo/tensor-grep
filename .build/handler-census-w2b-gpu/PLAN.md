# Implementation Plan: HANDLER-CENSUS-W2-b (GPU Backends)

Executor: unlazy
Approved: claude-opus-5 2026-09-03 APPROVED run-w2b-gpu-001

## Objective
In-slice harden and complete the disposition ledger for the GPU backends (`backends/cudf_backend.py`, `backends/torch_backend.py`, `backends/cybert_backend.py`), narrowing 1 silent swallow, adding log disclosures to 4 silent degradation points, reducing the broad handler ceiling from 267 to 266, and enrolling all 3 modules into `_EXPLICIT_AUDITED_MODULES`. This completes ARCH-002 across all 9 backend modules and 46 total backend broad handlers.

## File Map
- Modify: `src/tensor_grep/backends/cybert_backend.py` (import binascii, narrow line 146 b64 handler; add logger.debug on line 377)
- Modify: `src/tensor_grep/backends/cudf_backend.py` (add logger disclosures on lines 71, 75, 148)
- Modify: `tests/unit/test_silent_failure_hardening.py` (drop TOTAL_BROAD_HANDLERS_CEILING 267 -> 266 per Rule A137)
- Modify: `tests/unit/test_handler_dispositions.py` (add 3 GPU backend modules to `_EXPLICIT_AUDITED_MODULES`)
- Modify: `docs/audits/2026-08-20-handler-dispositions.json` (append 21 records: 10 INTENTIONAL-BOUNDARY, 11 LOGGED-DEGRADE)
- Modify: `docs/plans/HANDLER-CENSUS-W2.md` (reconcile W2-a SHIPPED, W2-c SHIPPED, W2-b COMPLETE 21/21)
- Modify: `backlog.md`
- Modify: `.orchestrator/state.json`

## Tasks

### Task 1: Product Hardening & Ratchet Adjustment (RED -> GREEN)
1. In `src/tensor_grep/backends/cybert_backend.py`:
   - Import `binascii`.
   - Narrow `deobfuscate_payload` from `except Exception:` to `except (ValueError, binascii.Error):`.
   - In `_classify_impl` line 377: add `logger.debug("CyBERT traced inference failed, retrying inference without tracer: %s", exc)` before retry.
2. In `src/tensor_grep/backends/cudf_backend.py`:
   - In `_process_chunk_on_device` lines 71 and 75: add `logger.debug("RMM device-specific reinitialize failed for device %s, falling back to default: %s", local_device_id, exc)` and `logger.warning("RMM default reinitialize failed: %s", exc2)`.
   - In `_device_context` line 148: add `logger.debug("CuPy device capability probe failed, using nullcontext: %s", exc)`.
3. In `tests/unit/test_silent_failure_hardening.py`:
   - Decrement `TOTAL_BROAD_HANDLERS_CEILING` from `267` to `266`.
   - Update the A137 arithmetic comment block.

### Task 2: Ledger Extension & Completeness Gate
1. In `tests/unit/test_handler_dispositions.py`:
   - Add `"backends/cudf_backend.py"`, `"backends/torch_backend.py"`, `"backends/cybert_backend.py"` to `_EXPLICIT_AUDITED_MODULES`.
2. In `docs/audits/2026-08-20-handler-dispositions.json`:
   - Append the 21 records with exact identity keys (7 cudf, 5 torch, 9 cybert).
   - Categories: 10 INTENTIONAL-BOUNDARY, 11 LOGGED-DEGRADE, 0 SILENT-SWALLOW.
   - Non-empty, distinct evidence and reason for each record; `hardened_in: "HANDLER-CENSUS-W2-b"` for the hardened sites, `null` for pure census boundaries.

### Task 3: Verification & Reconciliation
1. Run `pytest tests/unit/test_handler_dispositions.py tests/unit/test_silent_failure_hardening.py tests/unit/test_governance_doc_size_ratchet.py`.
2. Run `ruff check` and `mypy src/tensor_grep`.
3. Reconcile `docs/plans/HANDLER-CENSUS-W2.md`, `backlog.md`, `.orchestrator/state.json`, and `.build/handler-census-w2b-gpu/RECEIPTS.md`.
4. Commit as `fix(backends): harden GPU backend error disclosures and complete ARCH-002 census`.
