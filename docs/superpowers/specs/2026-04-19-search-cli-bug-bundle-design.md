# Search CLI Bug Bundle Design

## Goal

Fix the currently reproducible `tg search` correctness bugs in the Python search path without widening scope beyond search safety and CLI contract parity.

## Scope

This design covers four reported upstream symptoms:

1. `B1`: fixed-string directory search can crash when the `StringZillaBackend` reads invalid UTF-8 or binary content.
2. `B2`: structured output (`--json` / `--ndjson`) reportedly hangs.
3. `B3`: empty search patterns are accepted and behave like match-everything instead of being rejected as invalid input.
4. `B4`: nonexistent paths fall through as a silent search miss instead of a clear path error.

## Current Read

Local reproduction on `origin/main` shows:

- `B1` is real at the backend level. `StringZillaBackend.search()` and `_search_with_index()` open files with strict UTF-8 decoding and can raise `UnicodeDecodeError`.
- `B3` is real. `search_command()` accepts `""` as the positional pattern and proceeds with a match-everything search.
- `B4` is real in effect, but the exact symptom has drifted. Current behavior is a silent nonzero exit (`1`), not the reported silent `0`. It is still missing a useful path error and ripgrep-like usage distinction.
- `B2` does not reproduce on this branch. `tg search ... --json` returns immediately with structured output, and `tg search ... --ndjson` fails quickly with the existing native-binary requirement message. No code change should be made for `B2` without a failing reproduction.

## Design Decisions

### 1. Fixed-string binary handling should fail soft, not crash

`StringZillaBackend` will gain a small shared text-loading helper used by both the direct search path and the index-building path.

Behavior:

- If the file contains obvious binary data (NUL bytes) and neither `--text` nor `--binary` is active, skip it and return an empty `SearchResult`.
- If strict UTF-8 decoding fails and neither `--text` nor `--binary` is active, skip it and return an empty `SearchResult`.
- If `--text` or `--binary` is active, decode with replacement so the search can proceed without crashing.

This keeps the default behavior aligned with ripgrep’s “do not crash on binary” posture while preserving an explicit opt-in to search binary-like content.

### 2. Empty patterns are invalid input

`search_command()` will reject an empty positional pattern before pipeline setup and scanning.

Behavior:

- Print a concise error to `stderr`.
- Exit with code `2` to mark an invocation/usage error.

This prevents the current accidental match-everything behavior and makes the failure scriptable.

### 3. Missing paths should be reported before scanning

`search_command()` will validate every input path after argument parsing and before candidate collection.

Behavior:

- If any path does not exist, print a path-specific error to `stderr`.
- Exit with code `2`.

This keeps the difference between “no matches found” and “invalid invocation/input path” explicit.

### 4. Structured output gets a regression contract, not a speculative fix

Because `B2` does not currently reproduce, the safe action is a focused regression test that exercises `tg search --json` in a subprocess with a timeout and proves the command returns data promptly.

No serializer or flush changes should be made unless that test fails first.

## Non-Goals

- No native `tg` CLI protocol changes.
- No search output format redesign.
- No benchmark-claim changes in docs or paper.
- No broad encoding-system redesign beyond the minimum fixed-string safety fix.

## Verification

Required local gates:

- `uv run ruff check .`
- `uv run mypy src/tensor_grep`
- `uv run pytest -q`

Because this touches search behavior in a hot path, also run:

- `python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.search_cli_bug_bundle.json`
- `python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.search_cli_bug_bundle.json`

## Files Expected To Change

- `src/tensor_grep/backends/stringzilla_backend.py`
- `src/tensor_grep/cli/main.py`
- `tests/unit/test_stringzilla_backend.py`
- `tests/unit/test_cli_modes.py`
- `tests/e2e/test_routing_parity.py` or another focused subprocess CLI contract test if needed for `B2`
