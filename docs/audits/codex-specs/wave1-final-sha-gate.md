# Wave 1 Final-SHA Audit Spec

**Repo:** tensor-grep  
**SHA:** 722df38 (fix(audit): close Wave 1 verification gaps G1/G2/G3)  
**Model:** gpt-6-sol  
**Effort:** high  
**Sandbox:** read-only  
**Working dir:** C:\dev\projects\tensor-grep  

---

## Audit Scope

Final security + integrity gate on Wave 1 closeout artifacts (SHA 722df38):
1. `tests/unit/test_import_edges_baseline.py` — mutation-control test (G1)
2. `docs/audits/gitleaks-2026-09-30-full-history.md` — gitleaks report (G2)
3. `docs/audits/handler-census-gaps-2026-09-30.md` — handler census (G3)

## Prior Claims (validate, do not re-derive)

**Intent verification (Sonnet 5, VERIFIED):** All three artifacts deliver what MAP.md answer key requires.

**Adversarial audit (Droid glm-5.3-flash, AUDIT_CLEAR Round 3):** Three gaps identified in Round 1 (fixture validation, positive control rule names, scope clarity), all remediated and verified in Round 3. No further defect classes identified.

## Your Scope

Security-focused audit on the three artifacts:

1. **G1 Mutation-Control Test (lines 150-188)**
   - **Hostile mutation:** Does the test truly catch backward imports, or can a backward import slip through?
   - **Evidence demand:** Plant a backward edge that should fail; show the test fails; show removing it makes test pass
   - **Severity:** HIGH if test is a false gate; MEDIUM if edge case exists

2. **G2 Gitleaks Report (lines 1-90)**
   - **Hostile scenario:** Could a real AWS credential live in the repo undetected?
   - **Evidence demand:** Report names detection rules (aws-access-key-id, aws-secret-key-pattern); scan is full-history; positive control passed
   - **Severity:** CRITICAL if scan is incomplete; HIGH if rules don't cover AWS patterns

3. **G3 Handler Census (lines 1-120)**
   - **Hostile scenario:** Are there broad handlers hiding in audited modules that the scan missed?
   - **Evidence demand:** Scope states audited modules were scanned (9 backend); zero tuple-Exception handlers found; ledger is reconciled and complete
   - **Severity:** HIGH if audited scope was skipped; MEDIUM if tuple handlers exist but are pre-expected

## Severity Rubric

- **CRITICAL:** Credential could live undetected; mutation test is false gate; ledger contradicts live code
- **HIGH:** Hostile audit catches a real defect not addressed by fixes
- **MEDIUM:** Edge case or ambiguity that informs Wave 2 (not a shipping blocker)
- **LOW:** Documentation clarity (does not change verdict)

Do not raise issues without naming the file, line, and the observable consequence.

## Output

Verdict line format (LAST occurrence):

```
[codex-audit] DONE Wave 1 security gate critical=0 high=0 medium=0 low=0
RECOMMENDED: APPROVE (no shipping blockers; ready for dual VERIFIED/GO)
```

If defects exist:

```
[codex-audit] DONE Wave 1 security gate critical=1 high=2 medium=0 low=1
RECOMMENDED: REVISE — critical blocker at [file:line] (describe), HIGH at [file:line] (describe)
```

Append findings to `.build/deep-audit-verify/RECEIPTS.md` under **"Codex Sol Final-SHA Audit"** section.

## Constraints

- Do not switch model or enter interactive mode
- Do not modify production code
- Read-only sandbox; no commits
- Git operations read-only (status, log, diff only)
