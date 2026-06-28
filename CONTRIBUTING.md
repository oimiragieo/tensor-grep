# Contributing to tensor-grep

`tensor-grep` is a benchmark-governed, contract-heavy codebase. Changes should be small, measurable, and aligned with the release automation instead of relying on manual versioning or manual tag management.

## Local Validation

Run these before proposing a change:

```bash
uv run ruff check .
uv run ruff format --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

For release/workflow/package-manager changes, also run:

```bash
uv run python scripts/validate_release_assets.py
```

**Ruff preview split:** CI runs `ruff format --check --preview .` but only `ruff check .` (no `--preview`) for lint. Locally, always run `ruff format --preview` but never pass `--preview` to `ruff check` — preview lint rules such as RUF056 produce false failures that do not match CI. Note: running `ruff format` WITHOUT `--preview` is an ACTIVE REVERT — it rewrites preview-style lines back on disk, so the next CI `ruff format --check --preview` fails on lines you did not intend to touch. Always pass `--preview` to `ruff format`.

**Line endings:** `.gitattributes` pins `*.py` and `*.rs` to `eol=lf`. Use `git ls-files --eol` to audit actual on-disk endings; `git show` and `git cat-file -p` smudge the output and can report false CR.

**Decode the structured CI failure first:** When a CI run fails, open the failing check's structured JSON output before reading rich tracebacks. Theorizing from tracebacks without identifying the exact failing check wastes cycles — the structured output names the precise gate and often the file and line. This rule saved multiple CI round-trips during the June 2026 README-rewrite incident.

## Public Issue Intake

Use the GitHub issue forms for public bug reports, feature requests, questions, and documentation issues. Do not put secrets, private code, credentials, tokens, or undisclosed vulnerability details in public issues.

Security-sensitive reports belong in private vulnerability reporting:
<https://github.com/oimiragieo/tensor-grep/security/advisories/new>

New and edited public issues are classified by a deterministic triage workflow. The workflow labels area/type/priority, requests missing reproduction details when needed, and flags possible security-sensitive reports for private maintainer review. It does not call external AI services, run reporter-provided commands, open links, inspect attachments, or echo raw reporter content back into comments.

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
- A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state.
- Release versioning starts only after a release-bearing PR is squash-merged to `main`.
- Release work is not complete until main CI and semantic-release complete successfully, `publish-success-gate` passes when publishing is required, tags/main are fetched, local `main` is fast-forwarded to the release commit, and PyPI/public installer availability is verified when relevant.

## Documentation and Contract Changes

If you change workflow, release behavior, docs contracts, or package-manager assets, update the validator-backed tests too.

## Post-Release Docker Dogfood Gate

After a release lands on PyPI, run the post-release dogfood harness to verify the published binary — not just the local source tree:

```bash
# Run the battery against a tg already on PATH (e.g. an installed wheel)
python scripts/dogfood/dogfood_features.py
# Or clean-room via Docker: install the PUBLISHED version, then run the real binary
docker build --build-arg TG_VERSION=<version> -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood \
  && docker run --rm tg-dogfood
```

The harness at `scripts/dogfood/` installs the real PyPI wheel and runs every public `tg` command through the installed `tg` binary. This catches routing bugs that are invisible to `CliRunner`-based unit tests because the bootstrap front door (which forwards plain searches to ripgrep before the Typer app) is bypassed by `CliRunner`. A flag that works in unit tests can still crash with `rg: unrecognized flag` for real users if it is missing from the bootstrap or native front-door allowlists.

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
