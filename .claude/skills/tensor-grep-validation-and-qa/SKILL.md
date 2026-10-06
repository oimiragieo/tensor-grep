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
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
uv run --no-sync mypy src/tensor_grep
uv run --no-sync python scripts/file_size_budget.py --report
```

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
