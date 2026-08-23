# GATES — tensor-grep backlog closeout (2026-08-22)

Written BEFORE work per `unlazy` rule zero. Intentions do not survive a long context; files do.

**Scope honesty up front.** The operator's ask is "complete all backlog". The measured board is 28
rows / 17 unfinished, and **10 of those 17 cannot be closed by an agent at all** (5 CEO_GATED, 5
DEMAND_GATED). Gates below cover only what an agent can actually finish. Rows an agent cannot close
are listed under NOT-IN-SCOPE with the reason, not silently dropped.

---

## G1 — land the keystone (PR #1093, local CI harness)

- [ ] **G1.1** PR #1093 merged to main
  - CHECK: `gh pr view 1093 --json state -q .state`
  - EXPECT: `MERGED`
  - EVIDENCE: pending
- [ ] **G1.2** its main run reached terminal success (not cancelled)
  - CHECK: `gh run list --branch main --workflow=ci.yml --limit 1 --json conclusion -q '.[0].conclusion'`
  - EXPECT: `success`
  - EVIDENCE: pending

## G2 — land the in-flight docs (PR #1100)

- [ ] **G2.1** PR #1100 merged
  - CHECK: `gh pr view 1100 --json state -q .state`
  - EXPECT: `MERGED`
  - EVIDENCE: pending
- [ ] **G2.2** the campaign plan's revision 2 is on main, not stranded in a stash
  - CHECK: `git show origin/main:docs/plans/2026-08-22-blocked-row-unblock-campaign.md | grep -c "SUPERSEDED BY REVISION 2"`
  - EXPECT: `3`
  - EVIDENCE: pending

## G3 — the two CONFIRMED external dogfood defects

- [ ] **G3.1** `blast-radius-render` accepts `--deadline`, matching its sibling
  - CHECK: `uvx --from tensor-grep tg blast-radius-render --help 2>&1 | grep -c -- --deadline`
  - EXPECT: `1` (currently `0` — the defect)
  - EVIDENCE: pending
- [ ] **G3.2** the rg-order caveat is documented where an agent will read it
  - CHECK: `grep -rc "sort path" .claude/skills/tensor-grep/REFERENCE.md docs/CONTRACTS.md 2>/dev/null | awk -F: '{n+=$2} END{print (n>0)}'`
  - EXPECT: `1`
  - EVIDENCE: pending

## G4 — the 5 UNVERIFIED dogfood findings each get a verdict

Not "fixed" — **verified or refuted**, with a probe. An unverified finding must not be closed, and
must not be fixed either; that is how a wrong fix ships.

- [x] **G4.1** session cwd footgun — reproduced or refuted, probe recorded
  - EVIDENCE: REPRODUCED on published v1.111.7. `session open src` -> id
    `session-20260823004352130555-src-6dc6b0f9`; `session show <id>` WORKS from `src/`,
    returns `Session not found` from the repo root one level up. Mechanism found and it is
    worse than reported: TWO cwd-keyed stores (`src/.tensor-grep/sessions/` holds the id,
    `.tensor-grep/sessions/` has 67 files and 0 matches), so `list` from the root returns 64
    sessions NOT containing the one just opened -- a confidently wrong answer, not an error.
    Fix is feasible: the id already embeds its root token. Recorded in docs/BACKLOG.md.
- [ ] **G4.2** warm-path latency / `response_cache_hits=0` — measured
  - EVIDENCE: pending
- [ ] **G4.3** Windows AST `run` argv fragility — reproduced or refuted
  - EVIDENCE: pending
- [ ] **G4.4** `tg dogfood` version-skew FAIL — reproduced or refuted
  - EVIDENCE: pending
- [ ] **G4.5** LSP provider split-brain (`lsp_proof=true` on a native anchor) — reproduced or refuted
  - EVIDENCE: pending

## G5 — board honesty

- [ ] **G5.1** every file path cited by a BLOCKED row resolves, OR the row says the file is gone
  - CHECK: `grep -c "path_domain.rs" docs/TASK_BOARD.md`
  - EXPECT: `0` (the row must stop citing a file that does not exist)
  - EVIDENCE: pending
- [ ] **G5.2** F6's shipped evidence-signing slice is marked shipped, not "remaining"
  - EVIDENCE: pending

## G6 — hygiene (repeated at the end, not assumed)

- [ ] **G6.1** working tree clean, no stray artifacts
  - CHECK: `git status --porcelain | grep -v '^??' | wc -l`
  - EXPECT: `0`
  - EVIDENCE: pending
- [ ] **G6.2** ruff format --preview + ruff check clean on everything touched
  - CHECK: `uv run ruff check . 2>&1 | tail -1`
  - EXPECT: `All checks passed!`
  - EVIDENCE: pending
- [ ] **G6.3** the published artifact still installs and works from a CLEAN container
  - EVIDENCE: pending

---

## NOT IN SCOPE — with reasons, so nothing is silently dropped

| Row | Why an agent cannot close it |
|---|---|
| **#48, #72, #77, #131, #169** | CEO_GATED. #72 is a public claim; #169 is the only money item. Recommendations are already filed and Exa-grounded; the decision is the operator's. |
| **5 DEMAND_GATED rows** | Each needs a bounded demand MEASUREMENT before any code. Building first is the speculative-feature failure `instrumented-build-gate` exists to prevent. |
| **F8** | Its central file `rust_core/src/path_domain.rs` DOES NOT EXIST. Cannot be planned until re-scoped; the similarly-named `runtime_paths.rs` is a guess, not evidence. |
| **#89 / #90** | Need a real WSL host. The container removes the CARGO constraint, never the WSL one. |
| **F5** | Scoped by a glob (`rust_core/**`). Exact touch-points must be enumerated before it can be sequenced. |

## ABANDON log

(none yet — an abandoned gate gets a line here with its reason, never a silent drop)
