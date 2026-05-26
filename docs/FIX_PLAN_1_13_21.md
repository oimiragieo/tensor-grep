# v1.13.21 Dogfood Fix Plan

## Goal

Clear the two v1.13.20 dogfood carry-overs without changing the raw search,
cache, map, edit-plan, or MCP contracts that held in v1.13.20.

## Findings

1. `tg upgrade` can leave a previously running session daemon absent after the
   package/native-front-door refresh. Explicit `tg session daemon start .` works,
   so the daemon runtime is healthy; the missing piece is upgrade handoff.
2. Successful hybrid/LSP proof payloads still surface historical Pyright
   `AssertionError: SRE module mismatch` lines in `provider_status.*.stderr_tail`.
   External evidence points to this class of traceback as a Python environment
   / stdlib mismatch, especially when a child process inherits interpreter
   variables such as `PYTHONHOME`.

Research anchor:

- CPython issue `python/cpython#124114` and uv PR `astral-sh/uv#17821` both tie
  `SRE module mismatch` to mismatched Python runtime/stdlib state or inherited
  Python environment.

## Implementation Plan

### 1. Upgrade Daemon Handoff

Files:

- `src/tensor_grep/cli/main.py`
- `tests/unit/test_cli_modes.py`

Steps:

1. Add a failing test that simulates a running daemon before `tg upgrade`, a
   stopped/stale daemon after the package/native refresh, and asserts upgrade
   calls `start_session_daemon()` for the pre-upgrade status `root` value. This
   matters because daemon status can discover a nearby child/parent root.
2. Implement a small helper that snapshots `get_session_daemon_status(".")`
   before upgrade and, after a successful upgrade, restarts the daemon only when
   it had been running and no live daemon is visible.
3. Add a negative test and do not start a daemon for users who did not already
   have one.
4. Carry the same pre-upgrade daemon root into the scheduled Windows
   self-upgrade helper so locked `tg.exe` upgrades can restart a daemon after
   the background install completes.

### 2. LSP SRE Mismatch Hygiene

Files:

- `src/tensor_grep/cli/lsp_provider_setup.py`
- `src/tensor_grep/cli/lsp_external_provider.py`
- `tests/unit/test_lsp_provider_setup.py`
- `tests/unit/test_lsp_external_provider.py`

Steps:

1. Add a failing test proving managed LSP provider launches strip inherited
   `PYTHONHOME`, `PYTHONPATH`, `VIRTUAL_ENV`, and `__PYVENV_LAUNCHER__` while
   preserving the managed Node/bin PATH entries.
2. Update `managed_provider_env()` to remove those interpreter-specific
   variables for managed providers only. Add a companion test proving path or
   custom providers keep those variables unchanged.
3. Change successful LSP-proof status handling so SRE/ABI mismatch diagnostics
   move to `provider_warnings` and remediation metadata, while `stderr_tail`
   remains empty/suppressed for the successful current proof payload. Preserve
   unrelated non-SRE stderr in `provider_recent_stderr` so non-current
   diagnostics are not silently dropped.
4. Keep failed/unhealthy provider statuses explicit; do not hide current
   request errors or stderr when proof is false.

## Validation

Targeted local gates:

- `uv run pytest tests/unit/test_cli_modes.py::<upgrade test> -q`
- `uv run pytest tests/unit/test_lsp_provider_setup.py::<env test> tests/unit/test_lsp_external_provider.py::<stderr test> -q`
- `uv run ruff format --check --preview <touched files>`
- `uv run ruff check <touched files>`
- `uv run mypy src/tensor_grep`

Release gates:

- Open a PR and let GitHub CI run.
- Merge only after CI is green.
- Verify the next semantic-release tag and public install proof before updating
  repo/skill release docs.
