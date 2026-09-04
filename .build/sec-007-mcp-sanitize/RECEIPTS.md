# Receipts: SEC-007 (MCP Wire Error Sanitization)

## Gate Receipts
- G1: Scope verification — 53 AST str(exc) sites mapped across 3 modules post-sanitization (PASS)
- G2: Sanitization architecture — 23/23 tests pass in test_mcp_error_sanitization.py (PASS)
- G3: Confinement & fail-closed preservation — 78/78 tests pass across error sanitization and w1a fail closed suites (PASS)

## Model Audits
- Fable 5.1 Plan Audit: CHANGES_REQUIRED (reconciled Class C preservation, session tool message shapes, poison fixture)
- Opus 5 Plan Iteration: APPROVED (run-sec007-001)
- Droid GLM-5.3-Flash Diff Audit: AUDIT_CLEAR (16 broad arms sanitized, 0 leaks, full trace on stderr)

VERIFIED sha=pending dual_go=pending
