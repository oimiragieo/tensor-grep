# Receipts: HANDLER-CENSUS-W2-b (GPU Backends)

## Gate Receipts
- G1: Scope verification — 21 broad handlers across 3 GPU backend files (PASS)
- G2: Ledger completeness and schema validation — 11/11 tests pass in test_handler_dispositions.py (PASS)
- G3: Silent failure hardening ratchet — 2/2 tests pass, ceiling ratcheted 267 -> 266 (PASS)

## Model Audits
- Fable 5.1 Plan Audit: CHANGES_REQUIRED (resolved with in-slice hardening design)
- Opus 5 Plan Iteration: APPROVED (run-w2b-gpu-001)
- Droid GLM-5.3-Flash Diff Audit: AUDIT_CLEAR (176 records total, 11 LOGGED-DEGRADE, 10 INTENTIONAL-BOUNDARY, 0 SILENT-SWALLOW)

VERIFIED sha=pending dual_go=pending
