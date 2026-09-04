# Receipts: SEC-007 (MCP Wire Error Sanitization)

## Gate Receipts
- G1: Scope verification — 30 AST str(exc) sites mapped post-sanitization (PASS)
- G2: Sanitization architecture — 15/15 tests pass in test_mcp_error_sanitization.py (PASS)
- G3: Confinement preservation — 175/175 tests pass in test_mcp_server_path_confinement.py (PASS)

## Model Audits
- Fable 5.1 Plan Audit: CHANGES_REQUIRED (reconciled Class C preservation, session tool message shapes, poison fixture)
- Opus 5 Plan Iteration: APPROVED (run-sec007-001)
- Droid GLM-5.3-Flash Diff Audit: AUDIT_CLEAR (16 broad arms sanitized, 0 leaks, full trace on stderr)

VERIFIED sha=pending dual_go=pending
