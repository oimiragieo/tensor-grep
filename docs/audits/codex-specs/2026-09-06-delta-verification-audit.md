# Codex Sol Delta Verification — fixes for the 2026-09-05/06 session audit findings

**Working dir:** `C:\dev\projects\tensor-grep` (repo root, `main` @ `4d1e3e9`)
**Model:** `gpt-5.6-sol`, `model_reasoning_effort="high"`
**Mode:** read-only. Do not edit, do not run git write commands, do not enter interactive mode.

## Context

Your own prior audit (`docs/audits/codex-specs/2026-09-06-session-fanout-audit.md`, result in
`docs/audits/codex-specs/2026-09-06-session-fanout-audit-RESULT.md` if it exists, otherwise the
final message is in this session's history) found 3 CRITICAL, 3 HIGH, 4 MEDIUM issues. A Claude
Sonnet 5 orchestrator independently verified and fixed all 3 CRITICALs. This audit checks whether
those fixes actually close the gaps you found, and re-examines the still-open PR's current state.

## CRITICAL #1 — S1 verify_edit_ticket (on `main`, merged)

Your finding: "S1's verifier unconditionally returns PASS when supplied paths are allowed; it
never checks filesystem fingerprints, ticket identity, validation, or tree state."
`src/tensor_grep/cli/edit_ticket_service.py:94`, `:114`.

Claimed fix, commit `86caffc` on `main`: `build_edit_ready_ticket` now fingerprints the WHOLE
tree via a new `_walk_tracked_files` helper (not just `allowed_files`); `verify_edit_ticket`
recomputes current fingerprints and compares against the ticket's stored `pre_edit_fingerprints`,
flagging (a) any file that drifted without being declared in `modified_files`, and (b) any
declared file whose fingerprint didn't actually change.

**Verify:** read `src/tensor_grep/cli/edit_ticket_service.py` in full as it exists NOW on `main`.
Does the fix actually close both attack scenarios you'd expect from your own finding? Specifically:
- An agent silently modifies a file OUTSIDE `allowed_files` and does not declare it in
  `modified_files` — does `verify_edit_ticket` now catch this?
- An agent claims to have modified a file in `modified_files` but never actually touched it — does
  it now catch this?
- Is there still a gap: e.g., does `verify_edit_ticket` correctly handle a NEW file created during
  the edit (not present in `ticket.pre_edit_fingerprints` because it didn't exist at ticket-build
  time)? Read the code and reason about whether a new undeclared file would be caught.
- Is `_walk_tracked_files` genuinely walking the same file set at build time and verify time (same
  exclusion rules), or could a discrepancy (e.g. a `.gitignore`-style skip, a race between build and
  verify) cause a false PASS?

## CRITICAL #2 — P3 injector-bypass scope claim (PR #1135, still open)

Your finding: "PR #1135 does not inject the incompleteness envelope into all MCP responses;
session tools bypass the injector." `src/tensor_grep/cli/mcp_server.py:4005`, `:4046`.

Claimed fix, commit `dd8b9e8` then `f00f4aa` on branch `feat/p3-mcp-incompleteness-envelope`
(worktree at `C:\dev\projects\tensor-grep-p3-mcp-envelope`): fixed 3 sites total —
`tg_session_open`, `tg_session_list`, and a third one you didn't name, a `tg_query` multi-root
aggregate success path that built its response via `_meta_envelope(...)` + bare `json.dumps`.
Claimed final state: 38 sites routed through `_self._inject_mcp_contract_fields`, 42 raw
`json.dumps` sites remaining, all 42 judged (by the fixing agent, not independently by you) to be
legitimate error/meta-helper payloads inside `except` blocks that don't need the envelope.

**Verify from the actual PR worktree** (`C:\dev\projects\tensor-grep-p3-mcp-envelope`, branch
`feat/p3-mcp-incompleteness-envelope`):
- Run `grep -n "return json.dumps(" src/tensor_grep/cli/mcp_server.py` yourself and independently
  spot-check AT LEAST 10 of the 42 remaining bare-json.dumps sites (not the ones already named as
  fixed). For each, read enough context to judge: is this genuinely an error/meta-helper payload
  that doesn't need `incomplete{}`, or is it actually a real tool SUCCESS response that still
  bypasses the injector? Name any you disagree with, citing file:line and your reasoning.
- Confirm the 3 claimed fixes actually route through the injector as claimed (read the exact lines
  around `tg_session_open`, `tg_session_list`, and the `tg_query` aggregate path).

## CRITICAL #3 — P3 truncation-signal blind spot (PR #1135, still open, same worktree)

Your finding: "PR #1135 reports real scan-capped responses as complete because it ignores existing
nested truncation signals." `src/tensor_grep/cli/incompleteness.py:141`, `mcp_server.py:3649`.

Claimed fix, commit `7cf88d4`: `unified_incomplete_envelope()` in
`src/tensor_grep/cli/incompleteness.py` now derives `status` as
`result_incomplete OR truncated`, not `result_incomplete` alone.

**Verify:** read the current `unified_incomplete_envelope` function. Are there OTHER nested
incompleteness signals in this codebase beyond `result_incomplete` and `truncated` that a tool
response might carry and that this function still misses? Grep `mcp_server.py` for
`"incomplete_reason"`, `"scan_limit"`, `"partial"`, `"omitted_"` field names used as top-level
payload keys elsewhere, and judge whether any of THOSE should also feed into the unified envelope
but currently don't (a genuine remaining gap) versus are already covered by `incomplete_reason`
(which the function does already read).

## Severity rubric (same as before)

CRITICAL: a fail-closed/security contract is still broken after the claimed fix. HIGH: the fix is
incomplete (closes the named scenario but leaves an adjacent one open) or a claim above could not
be confirmed. MEDIUM: style/completeness nit in the fix itself. Every finding needs `file:line`.

## Output

Write to `docs/audits/codex-specs/2026-09-06-delta-verification-RESULT.md` if your sandbox allows
file writes; if it is read-only, put the FULL structured findings in your final message instead
(do not lose the content to a failed write — same failure mode as your last audit, which the
orchestrator had to recover from your `-o`/last-message output).

End with exactly one of:
`RECOMMENDED: FIXES CONFIRMED` (all 3 CRITICALs genuinely closed, PR #1135 injector-routing claim
holds under your own spot-check)
`RECOMMENDED: STILL OPEN — <one-line summary of what remains broken>`
