# Contributing to tensor-grep

`tensor-grep` is a benchmark-governed, contract-heavy codebase. Changes should be small, measurable, and aligned with the release automation instead of relying on manual versioning or manual tag management.

## Local Validation

Run these before proposing a change:

```bash
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

For release/workflow/package-manager changes, also run:

```bash
uv run python scripts/validate_release_assets.py
```

## Performance Discipline

- Start with a failing test when behavior changes.
- Use the smallest defensible change.
- Run the relevant benchmark for hot-path changes before claiming a speedup.
- Reject regressions even if the code is otherwise clean.

## Pull Request and Release Intent

- Use conventional titles so semantic-release can infer the bump:
  - `feat: ...` => minor release
  - `fix: ...` or `perf: ...` => patch release
  - `feat!: ...` or `fix!: ...` => major release
  - `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release
- Use **Squash and merge** for release-bearing PRs so the validated PR title becomes the commit subject on `main`.
- Do not manually create release tags while semantic-release is active.

## Documentation and Contract Changes

If you change workflow, release behavior, docs contracts, or package-manager assets, update the validator-backed tests too.

Important surfaces include:
- `tests/unit/test_release_assets_validation.py`
- `tests/unit/test_public_docs_governance.py`
- `tests/unit/test_enterprise_docs_governance.py`
- `docs/CI_PIPELINE.md`

## Enterprise Docs

Before calling a release enterprise-ready, keep these documents aligned with the shipped behavior:
- `README.md`
- `docs/CI_PIPELINE.md`
- `docs/SUPPORT_MATRIX.md`
- `docs/CONTRACTS.md`
- `docs/HOTFIX_PROCEDURE.md`
- `docs/EXPERIMENTAL.md`
- `docs/RELEASE_CHECKLIST.md`
