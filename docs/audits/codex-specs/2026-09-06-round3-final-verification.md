# Codex Sol Round-3 Final Verification — PR #1135 (P3 MCP incompleteness envelope)

**Working dir:** `C:\dev\projects\tensor-grep-p3-mcp-envelope` (worktree, branch
`feat/p3-mcp-incompleteness-envelope`, HEAD `0b949f0`)
**Model:** `gpt-5.6-sol`, `model_reasoning_effort="high"`
**Mode:** read-only. Do not edit, do not run git write commands.

## Context

Two prior Codex Sol audit rounds on this PR each found real defects the previous round's fix
missed (see `docs/audits/codex-specs/2026-09-06-session-fanout-audit.md` and
`2026-09-06-delta-verification-audit.md` for full history — read them if useful context, but
your job here is to verify the CURRENT code, not re-litigate old rounds). Total fixes applied
across both rounds, all in `src/tensor_grep/cli/incompleteness.py` and
`src/tensor_grep/cli/mcp_server.py`:

1. `tg_mcp_capabilities` bare-call fix (pre-round-1)
2. 3 injector-routing bypasses fixed: `tg_session_open`, `tg_session_list`, `tg_query` aggregate
3. `unified_incomplete_envelope` extended to check: `result_incomplete`, `truncated`, `partial`,
   nested `results_by_root[*].incomplete`, and now (most recent commit `0b949f0`)
   `scan_limit.truncation_cause`/`scan_limit.budget_remediable` in preference to generic fallbacks
4. `tg_search(count_matches=true)` now stamps a top-level `truncated` field on a `max_repo_files`
   cap, matching the `is_empty` branch's existing pattern

## Your task

Read `src/tensor_grep/cli/incompleteness.py`'s `unified_incomplete_envelope` function AS IT
EXISTS NOW (do not trust the summary above — read the real file) end to end, and independently
answer: **is there any OTHER incompleteness/truncation signal shape in `mcp_server.py` that this
function still does not check?**

Method: grep `mcp_server.py` for every top-level JSON key name that could plausibly signal
incompleteness — `result_incomplete`, `truncated`, `partial`, `omitted_`, `scan_limit`,
`incomplete_reason`, `possibly_truncated`, `deadline_limit`, `capped`, `unreadable` — and for each
hit, trace whether that field's payload construction site ALSO routes through
`_inject_mcp_contract_fields` (if not, that's a routing gap, not an envelope-logic gap — note it
separately). For fields that DO route through the injector, check whether
`unified_incomplete_envelope` reads that specific field or a sibling of it.

Also independently re-verify (do not just trust the commit messages):
- Run `grep -n "return json.dumps(" src/tensor_grep/cli/mcp_server.py | wc -l` and
  `grep -n "_self._inject_mcp_contract_fields\|_inject_mcp_contract_fields(json" src/tensor_grep/cli/mcp_server.py | wc -l`
  yourself. Confirm the counts are still consistent with "no new tool-success bypass" (you don't
  need to re-classify all sites again if the counts match your round-2 findings — only dig deeper
  if the counts changed unexpectedly).
- Spot-check 5 DIFFERENT raw-json.dumps sites than the 15 you checked in round 2 (pick ones you
  haven't looked at) to widen coverage.

## Output

Read-only sandbox likely means no file write — if so, put the full findings in your final
message (do not lose content to a failed write, as happened in round 1).

End with exactly one of:
`RECOMMENDED: MERGE-READY` (no further real gaps found)
`RECOMMENDED: STILL OPEN — <one-line summary of the remaining real gap, with file:line>`
