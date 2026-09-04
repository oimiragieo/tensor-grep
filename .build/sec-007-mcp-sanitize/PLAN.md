# Implementation Plan: SEC-007 (MCP Wire Error Sanitization)

Executor: unlazy
Approved: claude-opus-5 2026-09-03 APPROVED run-sec007-001

## Objective
Sanitize all 16 broad `except Exception` internal error exposures on the MCP wire in `src/tensor_grep/cli/mcp_server.py` and narrow path confinement disclosures, preventing absolute filesystem path and credential leaks to AI agent callers while strictly preserving all existing structured error envelopes, backend fail-closed contracts (Class C), and W1-a disclosure contracts.

## File Map
- Modify: `src/tensor_grep/cli/mcp_server.py` (sanitize 16 Class A broad internal error arms, introduce PathConfinementError with constant public message, log resolved paths to stderr)
- Modify: `tests/unit/test_mcp_error_sanitization.py` (add poison injection tests, confinement path disclosure tests, and structural AST ratchet)
- Modify: `docs/plans/HANDLER-CENSUS-W2.md` (stamp SEC-007 COMPLETE with 16/2/38 disposition)
- Modify: `backlog.md`
- Modify: `.orchestrator/state.json`

## Invariants & Design Decisions
1. **Class C Preserved:** `BackendExecutionError` arms (`find_backend_error:2725` and `semantic_backend_error:3018`) are deliberate, curated fail-closed messages tested by `test_mcp_server_search.py:875` and `test_mcp_tg_find.py:198`. They remain untouched.
2. **Line 3752 Preserved:** `tracked_file_count_error` is pinned by `test_w1a_mcp_silent_swallow_fixes.py:172` and remains untouched.
3. **16 Class A Sites Sanitized:** All 16 broad `except Exception` arms returning internal errors are routed through `_sanitized_tool_error(tool_name, exc)` (or `_sanitized_tool_error(tool_name, exc)["message"]` for string-payload session helpers), logging full detail server-side to stderr while returning safe message to wire.
4. **Path Confinement Sanitization:** `_confine_read_path` and `_confine_write_path` raise `PathConfinementError(ValueError)` with constant public message `"{label} must stay within the MCP root (refused)"` and log the resolved target to stderr.
5. **Release Class:** `fix(mcp): sanitize raw exception messages on MCP wire` (patch release).

## Tasks

### Task 1: RED — Write Sanitization & Ratchet Tests
1. In `tests/unit/test_mcp_error_sanitization.py`:
   - Add `test_broad_mcp_handlers_never_echo_raw_str_exc_ast_ratchet`: AST walk over `mcp_server.py` asserting that across all broad `except Exception` or bare `except` blocks, zero arms call `str(exc)` (except the pinned W1-a line 3752).
   - Add `test_meta_tool_errors_do_not_leak_poison_trace_or_path`: Parameterized test across meta tools (`tg_repo_map`, `tg_context_pack`, `tg_edit_plan`, `tg_context_render`, `tg_agent_capsule`) injecting poison exception text and asserting `payload["error"]["message"]` does not leak poison text while stderr captures it.
   - Add `test_path_confinement_never_echoes_absolute_or_target_paths`: Verify `_confine_read_path` and `_confine_write_path` with outside paths emit constant public refusal message without leaking resolved absolute paths on the wire.
2. Run `pytest tests/unit/test_mcp_error_sanitization.py` to observe RED failures on the ratchet.

### Task 2: GREEN — Implement Sanitization in mcp_server.py
1. **Confinement Sanitization:**
   - Define `PathConfinementError(ValueError)`:
     ```python
     class PathConfinementError(ValueError):
         def __init__(self, label: str):
             super().__init__(f"{label} must stay within the MCP root (refused)")
     ```
   - In `_confine_read_path` and `_confine_write_path`, log `resolved` vs `anchor_resolved` to stderr and raise `PathConfinementError(label)`.
2. **Class A Broad Arms Sanitization (16 sites):**
   - Meta tools: `tg_repo_map:1540`, `tg_context_pack:1747`, `tg_edit_plan:1833`, `tg_context_render:1923`, `tg_agent_capsule:2014` -> `_sanitized_tool_error(tool_name, exc)`.
   - Session meta/legacy tools: `:2105`, `:2207`, `:2288`, `:2393`, `:2507`, `:2614`, `:3743`, `:3787`, `:3828`, `:3864`, `:3940` -> pass `_sanitized_tool_error(tool_name, exc)["message"]` to session error payloads.

### Task 3: Verification & Regression Check
1. Run `pytest tests/unit/test_mcp_error_sanitization.py -v`.
2. Run all MCP and W1-a suites: `pytest tests/unit/test_mcp*.py tests/unit/test_w1a*.py -q`.
3. Run `ruff check` and `mypy src/tensor_grep`.
4. Reconcile `docs/plans/HANDLER-CENSUS-W2.md`, `backlog.md`, `.orchestrator/state.json`, and `.build/sec-007-mcp-sanitize/RECEIPTS.md`.
