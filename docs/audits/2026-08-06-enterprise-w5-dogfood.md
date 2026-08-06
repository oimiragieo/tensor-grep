# Enterprise launch W5 — published-wheel dogfood (`tensor-grep==1.110.0`)

Date: 2026-08-06  
Artifact: `uvx --refresh-package tensor-grep --from tensor-grep==1.110.0 tg --version` → `tensor-grep 1.110.0`  
Host: Windows PowerShell / `py -3` (not WSL `uv` against the checkout `.venv` — A60)  
Scratch receipts: `C:\Users\Public\tg-w5-dogfood3-results.txt`  
Related merge: `#958` → `65d0195` (`test: lock prepare→evidence→review-bundle enterprise CUJ chain`)

## Verdict table

| Route | Arm | Result | Exit / structured field |
|---|---|---|---|
| `tg prepare … --json --out` | pos | PASS | `0`; `primary_target.symbol=calculate_late_fee` |
| `tg search PATTERN --json PATH` | pos | PASS | `0`; `total_matches=2`, `routing_backend=RipgrepBackend` |
| `tg search` no-match | neg | PASS | `1`; `total_matches=0` |
| `tg evidence emit --capsule --out` | pos | PASS | `0`; receipt file written; `revision.commit_sha` bound |
| `tg evidence emit --sign` (no key, **isolated HOME**) | neg | PASS | `1`; `error.code=signing_error`; **no** `--out` file |
| `tg evidence emit --sign` (operator HOME with ambient default key) | polluted | N/A | `0` + file written — **instrument contamination**, not product fail-open; ambient `~\.tensor-grep\keys\evidence_ed25519.key` present |
| `tg evidence keygen --out` | pos | PASS | `0`; private+pub written |
| `tg evidence emit --sign --signing-key` | pos | PASS | `0`; signed receipt written |
| `tg review-bundle create --manifest --receipt` | pos | PASS | `0`; bundle written |
| `tg review-bundle verify --against HEAD --min-receipts 1` | pos | PASS | `0`; `"valid": true` |
| `tg review-bundle verify --against deadbeef… --min-receipts 1` | neg | PASS | `1`; `"valid": false` |
| `tg ledger claim --files` | pos | PASS | `0`; `claim.files=["billing.py"]` |
| `tg ledger list` | smoke | PASS | `0`; claim appears in `claims[]` |

## Findings (honest, not flattened)

1. **Bare `uvx … tensor-grep==1.110.0` has no `semantic` extra** — `tg find` warns `model2vec not installed` and returns empty matches with `rank_fallback_reason`. Enterprise CUJ dogfood correctly used `prepare` / `search` / `evidence` / `review-bundle` / `ledger`, not `find`, for the launch bar. Document install path: `tg install-dense` or `tensor-grep[semantic]` when NL find is required.
2. **`--sign` no-key RED is only discriminative with an isolated home / cleared default key path.** Operator machines with `~\.tensor-grep\keys\evidence_ed25519.key` make a naive “no `TG_EVIDENCE_SIGNING_KEY`” probe green while still signing. The CUJ test already pops the env var; dogfooders must also neutralize the default key location (isolated `USERPROFILE`/`HOME`, or rename the default key for the probe).
3. **Hand-rolled rewrite-audit manifests for `review-bundle create` must satisfy checksum/schema expectations** — this dogfood used a minimal valid-looking JSON; production CI should prefer manifests emitted by the rewrite/audit path, as the CUJ test does.
4. **#958 CUJ lock is on `main` (`65d0195`)** — integration coverage for prepare→signed evidence→review-bundle verify (incl. strip/wrong-`--against`/no-key REDs) now gates the tree; published-wheel dogfood above is complementary route evidence, not a substitute for that CI test.

## Explicit non-claims

- Does not clear W3 rust/e2e, W4 Task 2A, MCP wire-contract fence, #169 FINANCIAL_HOLD, or CEO_GATED packets.
- Does not claim public GPU promotion or dense-find quality on the bare wheel.

## Follow-ups (backlog-shaped, not auto-built)

- Dogfood/docs note: isolate evidence signing default key when probing `--sign` fail-closed.
- Optional: TASK_BOARD reconcile stamp still may lag PyPI after `v1.110.0` — refresh on next docs reconcile if freshness gate complains (tolerance exists).
