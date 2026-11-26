---
name: tensor-grep-validation-and-qa
description: Select focused checks, verify installed entry points, and report tensor-grep validation against the exact artifact tested.
---

# Validate a change

Read [CONTRIBUTING.md](../../../CONTRIBUTING.md) and the relevant CI workflow.
Confirm the Python source and compiled extension belong to the intended
environment before running tests.

## Local checks

```text
cargo fmt --manifest-path rust_core/Cargo.toml --all -- --check
cargo clippy --manifest-path rust_core/Cargo.toml --all-targets -- -D warnings
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
uv run --no-sync mypy src/tensor_grep
uv run --no-sync python scripts/file_size_budget.py --report
uv run --no-sync python scripts/bare_call_ratchet.py --report
```

Note: `scripts/file_size_budget.py` also enforces line limits on `rust_core/tests/*.rs`.

After rebuilding the Rust extension (`uv pip install --no-deps --reinstall .`),
ensure no stale `src/tensor_grep/rust_core*.pyd` / `.so` shadows `.venv`
`site-packages` when `pytest` runs with `pythonpath = ["src"]` (copy the newly
built extension into `src/tensor_grep/` or remove the stale copy).

When touching `except Exception` blocks, loop `try/except OSError:` handlers,
PyO3 error formatting, or renaming/moving handler functions, also run:

```text
pytest -q tests/unit/test_handler_dispositions.py tests/unit/test_silent_loss_census_ratchet.py tests/unit/test_extractor_consumer_census.py
```

When touching `subprocess` calls, text decoding, or `_TG_MCP_SERVER_CONTRACT_VERSION`:

- Keep `_TG_MCP_SERVER_CONTRACT_VERSION = "X.Y.Z"` on a single line in `src/tensor_grep/cli/mcp_server.py` and synced in `docs/harness_api.md`.
- Prefer reusing an existing shared subprocess helper (or confined direct file reads) instead of adding a new production `subprocess` callsite, because `test_subprocess_decoding_guard.py` pins the production sink ceiling.
- Regenerate the subprocess policy inventory with `uv run --no-sync python scripts/check_subprocess_decoding.py --write-inventory`.
- Run `uv run --no-sync pytest -q tests/unit/test_subprocess_decoding_guard.py tests/unit/test_mcp_contract_version_docs_are_pinned.py`.

When touching imports in `src/tensor_grep/` or default MCP tool exposure (`TG_MCP_LEGACY_TOOLS`):

- Avoid dynamic `importlib.import_module(variable)` calls in `src/tensor_grep/` (use explicit literal allowlist dispatch) and run `uv run --no-sync pytest -q tests/unit/test_import_edges_baseline.py`.
- When modifying default MCP tools or schemas, run `uv run --no-sync pytest -q tests/unit/test_mcp_compact_parity.py tests/unit/test_mcp_error_sanitization.py tests/unit/test_trust_parity.py tests/unit/test_checkpoint_labels.py tests/unit/test_citations_output.py`.

In Rust `anyhow` error checks, use `err.is::<T>()` (not `err.chain().any(|c| c.is::<T>())`)
to match `.with_context(|| T(...))` wrappers, and place diagnostic tags after
canonical error prefixes (e.g., `"Rust search failed: [Tag] ..."`) so prefix
assertions keep matching.

Run focused pytest files for the changed behavior with an external process
timeout. On systems providing GNU timeout, for example:

```text
timeout 120 uv run --no-sync pytest -q tests/unit/test_lang_registry.py
```

Use an equivalent bounded process runner on Windows; Windows `timeout.exe`
is a delay utility, not a test timeout wrapper. Do not assume pytest's
`--timeout` option exists unless its plugin is installed.

## Select behavioral evidence

- Routing changes: test the installed CLI as well as direct Python handlers.
- Ranking changes: pin unrelated order and test minimal output budgets.
- State changes: use disposable fixtures for create, refresh, restore, and cleanup.
- Failure handling: assert the intended error, not merely any nonzero exit.
- Concurrency: use bounded synchronization rather than timing assumptions.
- Optional features: test available and unavailable dependencies.

Use CI for broad platform matrices and expensive builds. Record the exact source
SHA or installed artifact version, command, outcome, and limitations. A green
test on one checkout does not validate another checkout or a published wheel.
Make performance claims only from reproducible measurements of the named artifact.
