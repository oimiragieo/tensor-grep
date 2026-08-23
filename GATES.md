# GATES — tensor-grep backlog closeout (2026-08-22 → 23)

Written BEFORE work per `unlazy` rule zero. Intentions do not survive a long context; files do.

**FORMAT NOTE (2026-08-23) — the gates file was itself a broken instrument.** Revision 1 nested
`CHECK:`/`EXPECT:`/`EVIDENCE:` under markdown bullets (`  - CHECK: ...`). The checker never parsed
them, so every `CHECK` command silently did not run, and three gates carrying real measured evidence
reported *"checked but EVIDENCE pending"*. The plain `gate-check.mjs GATES.md` summary shows only
`UNMET: 16` — the per-gate reason is visible ONLY via `--status`. I nearly reported that 16 as fact.
**A gates file in the wrong shape returns a believable number while measuring nothing.**

I then broke it a SECOND way while "fixing" it — removing the indentation entirely — because I
matched the template by eye instead of reading the parser. The contract is
`ATTR_RE = /^\s+(CHECK|EXPECT|EVIDENCE):/` in `gate-check.mjs`: **leading whitespace is REQUIRED,
and the line must not be a markdown bullet.** Reading the regex took one command and settled what
two rounds of guessing did not. With the correct shape the ledger went 16-unmet-and-fake to
**5 met / 11 unmet**, and `G6.2` flipped ITSELF by running `ruff check` and writing its own
evidence — which is the whole point of a runnable gate.

**Scope honesty.** Live census against `origin/main:docs/TASK_BOARD.md` (not memory): **17 unfinished
rows — 5 CEO_GATED, 6 BLOCKED, 6 DEMAND_GATED.** **11 of 17 cannot be closed by an agent at all.**
Gates cover only what an agent can finish; unclosable rows are listed below with reasons.

---

- [x] G1.1: PR #1093 (local CI harness) merged to main
  CHECK: gh pr view 1093 --json state -q .state
  EXPECT: MERGED
  EVIDENCE: MERGED as 6dda05c7. Verified on the REVISION, not the merge report — `git ls-tree origin/main --name-only scripts/ci-local/` lists Dockerfile, entrypoint.sh, run.sh.

- [ ] G1.2: a main run after that merge reached terminal success
  CHECK: gh run list --branch main --workflow=ci.yml --limit 1 --json conclusion -q '.[0].conclusion'
  EXPECT: success
  EVIDENCE: pending

- [ ] G2.1: PR #1100 (onboarding guide + campaign plan) merged
  CHECK: gh pr view 1100 --json state -q .state
  EXPECT: MERGED
  EVIDENCE: pending

- [ ] G2.2: the campaign plan's revision 2 is on main, not stranded in a stash
  CHECK: git show origin/main:docs/plans/2026-08-22-blocked-row-unblock-campaign.md | grep -c "SUPERSEDED BY REVISION 2"
  EXPECT: 3
  EVIDENCE: pending

- [ ] G3.1: PR #1102 merged, so blast-radius-render's --deadline reaches users
  CHECK: gh pr view 1102 --json state -q .state
  EXPECT: MERGED
  EVIDENCE: pending

- [ ] G3.2: the rg-order caveat is documented where an agent will read it
  EVIDENCE: pending

- [x] G4.1: session cwd footgun — FIXED (was: reproduced or refuted, probe recorded)
  EVIDENCE: REPRODUCED on published v1.111.7. `session open src` -> session-20260823004352130555-src-6dc6b0f9; `show` WORKS from src/, "Session not found" from the repo root. TWO cwd-keyed stores (src/.tensor-grep/sessions/ holds it; .tensor-grep/sessions/ has 67 files, 0 matches), so `list` from root returns 64 sessions NOT containing the new one — a confidently wrong answer, not an error.
  FIXED on this branch (PR #1103): `_resolve_root` now anchors a subtree to the project root. Perturbation-proved -- revert anchoring and `test_subtree_resolves_to_the_project_root` FAILS; restore -> 9 passed.

- [x] G4.2: warm-path latency / response_cache_hits=0 — FIXED (was: measured)
  EVIDENCE: REPRODUCED, and it is a path-matching defect not a cold cache. Two identical `tg defs src ...` calls against a RUNNING daemon: 2505ms then 2702ms (slower), response_cache_hits=0 entries=0 AND cache_misses=0. Zero MISSES proves the daemon was never consulted. Control isolates it: query path `.` (== daemon root) -> misses=1 entries=1, and a second `.` query -> hits=1. The cache works; it is unreachable unless the query path exactly equals the daemon root -- so the docs' own advice to scope to a subdirectory silently disables the warm moat, with no honesty field explaining why.
  FIXED on this branch (PR #1103): the daemon derives its root through the same anchoring (`session_daemon.py` `_nearby_daemon_roots`/`get_session_daemon_status`). Pinned through the daemon's OWN entry point, not the resolver twice -- a codex round caught that first attempt. Perturbation: remove anchoring -> `test_anchoring_reaches_the_daemon_not_only_the_session_store` FAILS.

- [ ] G4.3: Windows AST `run` argv fragility — reproduced or refuted
  EVIDENCE: pending

- [x] G4.4: `tg dogfood` version-skew FAIL — reproduced or refuted
  EVIDENCE: REPRODUCED. pyproject=1.112.0 vs installed tg=1.111.7; FAIL, passed=15 failed=8, ALL eight public-version-*/public-doctor-*. Payload names the cause: agent_readiness.expected_version=1.112.0 while probes run the installed binary — the gate compares a CHECKOUT to a PUBLISHED ARTIFACT.

- [x] G4.5: LSP provider split-brain — reproduced or refuted
  EVIDENCE: NOT REPRODUCED, numbers OPPOSITE the report. `defs --provider lsp` -> lsp_count=1, fallback_used=False, full provider_agreement; `agent --provider lsp` -> lsp_proof=None, no provider_agreement key. Environment-dependent. The payload ASYMMETRY both runs agree on — agent ships no provider_agreement to cross-check — is the real finding.

- [ ] G5.1: no BLOCKED row cites a file that does not exist
  CHECK: git show origin/main:docs/TASK_BOARD.md | grep -c "path_domain.rs"
  EXPECT: 0
  EVIDENCE: pending

- [ ] G5.2: F6's shipped evidence-signing slice is marked shipped, not "remaining"
  EVIDENCE: pending

- [ ] G6.1: working tree clean of tracked modifications
  CHECK: git status --porcelain | grep -v "^??" | wc -l
  EXPECT: 0
  EVIDENCE: pending

- [x] G6.2: ruff check clean repo-wide
  CHECK: uv run ruff check . 2>&1 | tail -1
  EXPECT: All checks passed!
  EVIDENCE: All checks passed!

- [ ] G6.3: the published artifact installs and works from a CLEAN container
  EVIDENCE: pending

---

## NOT IN SCOPE — with reasons, so nothing is silently dropped

| Row | Why an agent cannot close it |
|---|---|
| **#48, #72, #77, #131, #169** (CEO_GATED) | Operator decision. #72 is a public claim; #169 is the only money item. Recommendations filed and Exa-grounded. |
| **#255, AST-DSL-PARITY, CONTINUOUS-REFRESH, DD-006, MCP-LEAN-DEFAULT, RUST-REPLACE-TOCTOU** (DEMAND_GATED) | Each needs a bounded demand MEASUREMENT before code. Building first is the speculative-feature failure `instrumented-build-gate` exists to prevent. |
| **F8** | Cites `rust_core/src/path_domain.rs`, which DOES NOT EXIST. Cannot be planned until re-scoped; `runtime_paths.rs` is a guess, not evidence. |
| **#89 / #90** | Need a real WSL host. The container removes the CARGO constraint, never the WSL one. |
| **F5** | Scoped by a glob (`rust_core/**`). Exact touch-points must be enumerated before sequencing. |
| **MCP-SURFACE** | Blocked on Task 2C — but measurement shows the contract version is a PYTHON one-liner (`mcp_server.py:188`), not cargo-blocked. Dependency needs re-deriving. |

## ABANDON log

(none — an abandoned gate gets a line here with its reason, never a silent drop)
