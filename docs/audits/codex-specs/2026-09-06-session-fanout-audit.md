# Codex Sol Audit — 2026-09-05/06 six-PR fan-out session

**Working dir:** `C:\dev\projects\tensor-grep` (repo root, checked out at `main`)
**Branch:** `main` @ `8e25c23` (verify with `git log -1`)
**Model:** `gpt-5.6-sol`, `model_reasoning_effort="high"`
**Mode:** read-only audit. Do NOT edit any file, do NOT run git write commands, do NOT enter interactive mode, do NOT switch model.

## Context (orchestrator's existing claims — validate, do not re-derive from scratch)

A Claude Sonnet 5 orchestrator ran a multi-PR fan-out session across 6 short-lived worktrees
(P2, S1-S6), each built by a separate `software-developer` subagent, then merged 6 of them to
`main` after CI went green:

| Merge commit | PR | What it claims to add |
|---|---|---|
| `a4e2d71` | #1133 (S1/S5) | `EditReadyTicketV1` fail-closed edit-ticket + verify-edit contract service — new file `src/tensor_grep/cli/edit_ticket_service.py` |
| `c762b1c` | #1129 (P2) | Wired `extract_imports_and_symbols` across all 10 registered languages in `repo_map.py` / `lang_registry.py` |
| `3e56c22` | #1132 (S3) | `next_action` machine protocol + budget envelope fields on `tg prepare`'s payload |
| `6b39ce2` | #1130 (S2) | Registration-aware polyglot (Go/Rust) symbol mapping for `tg diff-impact` |
| `d5c9354` | #1134 (S6) | `--why-ranked` match explanations + `install_state` envelope on `tg find`; also a follow-up commit `a5b774d` extracted `build_why_ranked_reasons`/`route_labels` into `src/tensor_grep/core/reranker.py` to hold a file-size ratchet on `main.py` |
| `5397bac` | (docs fix, no PR) | Resynced `docs/TASK_BOARD.md`'s version stamp with `docs/SESSION_HANDOFF.md` (both were drifted: `2026-08-30.1` vs `2026-09-05.1`) |
| `8e25c23` | (docs fix, no PR) | Fixed a self-contradicting language-count claim in `docs/tool_comparison.md` (line said "tg's 5", should be "tg's 10" post-P2) |

Two PRs are STILL OPEN with CI in flight as of this audit — DO NOT treat them as merged, but DO
review their diffs since they will likely land soon and the orchestrator wants advance findings:

- **PR #1131 (S4)** — branch `feat/s4-session-resume`, HEAD `cd3f8fd`. Adds `tg session-prepare`/
  `tg session-resume` CLI commands. A follow-up commit `b317bff` extracted
  `session_prepare_cmd`/`session_resume_cmd` into a new `src/tensor_grep/cli/session_resume_service.py`
  to hold the `main.py` file-size ratchet (13523 line baseline). A SECOND follow-up commit
  `cd3f8fd` raised `TOTAL_BROAD_HANDLERS_CEILING` in
  `tests/unit/test_silent_failure_hardening.py` from 338 to 340, on the claim that the two new
  `except Exception` handlers in `session_resume_service.py` (in
  `dispatch_session_prepare_cli`/`dispatch_session_resume_cli`) are "INTENTIONAL-BOUNDARY" —
  i.e. they disclose the error via `typer.echo(str(exc), err=True)` and exit non-zero rather than
  silently swallowing it. **Verify this claim directly by reading those two functions' full
  bodies** — confirm there is no code path where the exception is caught and a normal-looking
  success value is still returned.

- **PR #1135 (P3)** — branch `feat/p3-mcp-incompleteness-envelope`, HEAD `5e67ade`. Adds
  `src/tensor_grep/cli/incompleteness.py` with `unified_incomplete_envelope()`, routes it through
  the existing `_inject_mcp_contract_fields` choke point in `mcp_server.py`, bumps
  `_TG_MCP_SERVER_CONTRACT_VERSION` from `1.7.0` to `1.8.0`. A follow-up commit `5e67ade` fixed a
  bare-call-ratchet regression: `tg_mcp_capabilities` originally called
  `_inject_mcp_contract_fields(...)` as a bare name instead of `_self._inject_mcp_contract_fields(...)`,
  which the orchestrator says reopened a module (`mcp_server.py`) previously "RETIRED" at zero
  bare-calls-to-patched-symbols by a prior Route A split-floor conversion documented in
  `docs/design/2026-08-19-split-floor-escape.md`. **Verify**: (a) that `_self` is genuinely a
  module-level binding available at the call site (grep for where `_self` is assigned near the
  top of `mcp_server.py`), and (b) that this is the ONLY remaining bare call to a patched symbol
  in that file — run `python scripts/bare_call_ratchet.py` yourself from the PR's worktree if it
  exists (`C:\dev\projects\tensor-grep-p3-mcp-envelope`), otherwise state you could not verify and why.

## What to review

1. **`git show <SHA>` each of the 7 merge/fix commits above** on `main` (already merged, safe to
   inspect via git history without switching branches).
2. **The two open PRs' full diffs**: `git -C C:\dev\projects\tensor-grep-s4-session-resume diff origin/main...HEAD`
   and `git -C C:\dev\projects\tensor-grep-p3-mcp-envelope diff origin/main...HEAD` (both
   worktrees exist on disk at those paths — read directly, do not just trust this spec's summary).
3. Cross-cutting concerns specific to this repo (read `AGENTS.md`'s evidence-law section first
   if you have budget, otherwise apply directly):
   - **Silent-failure / fail-closed contract**: does any new `except Exception:` swallow an error
     and return a normal-looking value, rather than raising `BackendExecutionError` or disclosing
     via a returned error field / log / stderr?
   - **Ratchet-gate honesty**: for every test file edited (`test_file_size_budget.py`,
     `test_silent_failure_hardening.py`), does the edited assertion/pin genuinely match the
     REAL, current measured state of the code (re-derive the count yourself if you can run
     Python), or could the "fix" be a rationalized pin bump that papers over an actual regression?
   - **MCP contract-version discipline**: is `_TG_MCP_SERVER_CONTRACT_VERSION` bump (1.7.0→1.8.0)
     the ONLY version-sensitive site touched, or did the P3 change also require updates to other
     hardcoded version strings/tests that the orchestrator's summary doesn't mention (grep the
     whole repo for `"1.7.0"` and `1.7.0` to check for any the orchestrator missed)?
   - **New-file architecture fit**: does `session_resume_service.py` and `incompleteness.py`
     follow the existing pattern for how other CLI sub-modules are structured and registered
     (compare against `session_daemon.py`'s existing registration pattern in `main.py`), or do
     they introduce an inconsistent shape?
   - **Test quality, not just count**: for the newly added test files
     (`tests/unit/test_mcp_incomplete_envelope.py`, any new session-prepare/resume tests), do the
     tests assert real behavior (e.g. actually construct the envelope and check its fields) or are
     any of them tautological/vacuous (e.g. asserting a mock was called without checking the real
     return value)?

## Severity rubric

- **CRITICAL**: a fail-closed/security contract is broken (silent swallow on a fail-closed path,
  a secret/path leak reintroduced, an auth/permission check bypassed).
- **HIGH**: a ratchet/gate pin was raised to accept a real regression rather than to correctly
  classify a legitimate new pattern; a test is vacuous and would pass even if the feature were
  deleted; a contract-version bump missed a site that will break a real client.
- **MEDIUM**: architectural inconsistency (new module doesn't follow the established pattern);
  a claim in the orchestrator's summary above that you could not confirm from the code.
- **LOW**: style/naming nits, missing docstring, a comment that could be clearer.

Every finding MUST cite `file:line`. A finding with no citation is not a finding — omit it.

## Output

Write your full findings to `docs/audits/codex-specs/2026-09-06-session-fanout-audit-RESULT.md`
(create this file — do not edit any other file). Structure:

```
# Codex Sol Audit Result — 2026-09-05/06 session fan-out

## Verified claims
- <claim> — CONFIRMED, file:line

## Findings
### CRITICAL
- ...
### HIGH
- ...
### MEDIUM
- ...
### LOW
- ...

## Could not verify
- <claim> — reason (e.g. worktree not accessible, budget exhausted)

[codex-audit] DONE scope=session-fanout-2026-09-06 critical=<N> high=<N> medium=<N> low=<N> file=docs/audits/codex-specs/2026-09-06-session-fanout-audit-RESULT.md
```

End your final message with exactly one of:
`RECOMMENDED: APPROVE`
`RECOMMENDED: REVISE — <one-line summary of the blocking findings>`
