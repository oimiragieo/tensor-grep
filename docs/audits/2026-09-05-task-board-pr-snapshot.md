# Task-board PR snapshot (historical, 2026-09-05)

This is the dated table formerly in `docs/TASK_BOARD.md`. It is not a live PR queue;
use `gh pr list --state open` for current state.

| PR | Title | Type | State at snapshot |
|---|---|---|---|
| #1129 | `feat(repo_map): wire extract_imports_and_symbols across all 10 registered languages (P2)` | feat | OPEN (rebased on main `fbc5397`, 24 unit tests passing, CI running) |
| #1130 | `feat(diff-impact): add registration-aware polyglot symbol mapping test (S2)` | feat | OPEN (rebased on main `3ece043`, unit tests passing) |
| #1131 | `feat(session): add warm session prepare and resume contracts (S4)` | feat | OPEN (rebased on main `ec8ad83`, unit tests passing) |
| #1132 | `feat(prepare): add next_action machine protocol and budget envelope to prepare payload (S3)` | feat | OPEN (rebased on main `75f2e63`, unit tests passing) |
| #1133 | `feat(edit-ticket): implement EditReadyTicketV1 and fail-closed verify-edit contract service (S1)` | feat | OPEN (rebased on main `222683d`, unit tests passing) |
| #1134 | `feat(find): add --why-ranked match explanations and explicit install_state envelope (S6)` | feat | OPEN (rebased on main `ae822a6`, unit tests passing) |

PR #1128 `feat(diff-impact)` had merged at `7d2baa5` and released to PyPI as
`v1.116.0` via run `33995069360`.

PRs #872, #871 and #868 had all merged (#871 on 2026-07-31; #872 and #868 on
2026-08-01), but remained in an older board table as "CI running" / "BLOCKED — do
not merge". That stale-state failure is why the current board derives open PRs from
GitHub rather than treating a dated table as live.
