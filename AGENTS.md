# Contributor guidance

<!--
release_docs_current_tag: v1.124.0
-->

Tensor-grep combines text search, structural search, repository context, and optional GPU execution.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup and
[docs/architecture.md](docs/architecture.md) for the system overview.
This file describes requirements for human contributors and coding assistants.

## Working on a change

- Inspect the current implementation and relevant tests before editing. Preserve unrelated work.
- Use a focused branch and keep changes small enough to review. Check open pull requests before
  starting overlapping work. GitHub is the source of truth for pull-request status.
- Preserve public signatures and output contracts unless the change includes an explicit migration.
- Update user documentation when behavior changes. Distinguish available features from proposals.
- Keep private operating procedures, local configuration, unpublished research, and session notes
  outside the public repository. Only explicitly approved contributor skills belong in `.claude`.
- Remove temporary artifacts after verification. Preserve unmerged work; inspect branch ancestry
  before deleting branches and use `git branch -d` for normal cleanup.

## Architecture and public contracts

The Rust executable and Python bootstrap are separate entry points. Python also implements
orchestration, ranking, repository context, and MCP tools. A passing Python CLI test does not prove
that a published native executable routes the same request correctly.

## Backend Fail-Closed Contract

Every `ComputeBackend` must raise `BackendExecutionError` on execution failure rather than returning
an empty result. Reject unsupported requested semantics with `ConfigurationError`. A legitimate
fallback must disclose `fallback_reason` and `routing_reason`; do not present heuristic output as
model output or sidecar execution as native GPU execution.

AST engines have different query languages. Preserve the classifier and fail-closed behavior when
an ast-grep pattern requires the unavailable wrapper; tree-sitter is not a replacement for
ast-grep metavariable semantics. Describe supported structural operations as a useful validated AST
slice, with limits documented in [docs/CONTRACTS.md](docs/CONTRACTS.md).

Use scoped paths, globs, file types, and `--max-depth` for `tg search`.
`--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence limits, not general search
flags.

`rg` remains the baseline for cold text search. Compatibility covers the validated compatibility set
documented in [docs/CONTRACTS.md](docs/CONTRACTS.md), not every possible ripgrep invocation. Preserve
multi-project workspace and broad generated-root scan protections when changing search routing.

For `tg agent`, preserve the Actionable Context Capsule contract: executable line maps,
`context_consistency`, confidence, route rationale, omission counts, and checkpoint references.
Keep parser-backed, rg-backed, graph-derived, heuristic, and stale/uncertain evidence distinguishable.
Ask for clarification when the evidence cannot support a safe target selection.
`validation_plan[].detection` must identify runner evidence, including `package.json` evidence;
report when no runner evidence exists. `validation_alignment` must follow the primary target language
unless verified cross-language dependencies justify another runner.

## Adding a command, flag, or language

A top-level command requires these registration points:

1. `KNOWN_COMMANDS` in `src/tensor_grep/cli/commands.py`.
2. The `Commands` variant and dispatch arm in `rust_core/src/main.rs`.
3. `PUBLIC_TOP_LEVEL_COMMANDS` in `tests/e2e/test_routing_parity.py`.
4. The Typer command registration in `src/tensor_grep/cli/main.py`.

Search flags also need both `SEARCH_PYTHON_PASSTHROUGH_FLAGS` in
`rust_core/src/search_flag_registry.rs` and `_TG_ONLY_SEARCH_FLAGS` in
`src/tensor_grep/cli/bootstrap.py`. Test both entry points so unsupported flags cannot leak to ripgrep.
MCP tools need their own registration, schema, and response tests. Update
`_TG_MCP_SERVER_CONTRACT_VERSION` when their public request or response contract changes.
Use the language-support skill below for grammar, symbol, dependency, and validation registration.

## Security review

Changes involving writes, path confinement, backend execution, MCP, installers, native assets,
doctor probes, authentication, or index/session locks require an independent adversarial review
before merge. Review the exact commit and resolve behavior or security findings before merging.

Validate input before side effects. Preserve argument boundaries and end-of-options handling for
subprocesses. Choose explicit decoding policies for diagnostics and machine protocols. Never infer
installer authority from PATH or a caller-controlled location. Filesystem safety must account for
leaf symlinks, parent swaps, Windows junctions, and opened-object identity. Keep resource limits active
before expensive reads or child creation; define cleanup ownership on success and failure.
Security tests should include a valid positive control and exercise the intended refusal reason.

## Required local validation

Use the canonical platform-specific environment. Never let WSL `uv` replace a Windows `.venv`.
Verify the locations of the imported Python package and compiled `rust_core` extension, especially
when testing a worktree. Use an isolated tree for baseline comparisons because test configuration
can override `PYTHONPATH`.

Run the relevant bounded tests and these checks after the final edit:

```powershell
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
uv run --no-sync mypy src/tensor_grep
uv run --no-sync python scripts/file_size_budget.py --report
uv run --no-sync python scripts/bare_call_ratchet.py --report
```

Run targeted pytest suites through an external process timeout; do not assume `pytest-timeout` is
installed. Use at least 120 seconds for narrow suites and adjust for the workload. Run full test,
Rust, benchmark, and release-build matrices in CI or an approved resource-limited environment.
On shared hosts, avoid CPU-heavy local evaluation. Use `--preview` for Ruff formatting, not linting.

For ranking changes, pin the existing order before changing production code and assert only the
intended ordering difference. Concurrency tests should use bounded event handshakes rather than
scheduler timing. Tests for optional engines must control availability explicitly. Prove that new
regression checks fail for the intended defect, not an import error or missing dependency.

## Skills

These reviewed public skills live in `.claude/skills/`:

- `tensor-grep` — product usage and command selection.
- `tensor-grep-build-and-env` — development setup and environment verification.
- `tensor-grep-architecture-contract` — routing and compatibility contracts.
- `tensor-grep-add-language` — language support registration.
- `tensor-grep-validation-and-qa` — testing and reproducible validation.
- `tensor-grep-docs-and-writing` — public documentation maintenance.

## Dogfood follow-up workflow

Reproduce reported issues with disposable fixtures, add focused regression coverage, and replay
against the actual executable or published wheel. `CliRunner` bypasses bootstrap routing.
Use `python scripts/agent_readiness.py` and `tg dogfood` for the readiness checks relevant to the change.
The `agent-capsule-hardcases` check exercises target selection and context consistency on difficult
queries; keep this coverage when changing ranking or capsule output.
Windows launcher verification includes the quoted multi-word false-positive control in
`public-windows-launcher-quoted-patterns`. Record source, native executable, and wheel versions
separately. Do not use a successful sidecar call as proof of native GPU execution.

## Performance and release verification

Compare equivalent outputs before measuring speed. Separate cold text search, repeated indexed
queries, structural batches, and GPU workloads. Report corpus, environment, startup and transfer
costs, and the exact artifact tested. Broad performance claims require reproducible end-to-end data.

Follow `.github/workflows/ci.yml` and `pyproject.toml` for release behavior. A branch push or open PR
starts PR CI only. A release requires the merged commit's main CI, semantic-release, publication,
and artifact checks. Use conventional commit subjects with deliberate release intent.

Do not merge another change while the newest release-bearing main run is publishing; a new push
can interrupt or race publication. If the newest run completes unsuccessfully, prioritize its fix
before unrelated changes. Confirm the complete job population and final status on the exact SHA,
including `publish-success-gate` where applicable. After publication, run
`git fetch origin main --tags`, update local main without discarding work, and verify PyPI/public
installer availability and the published artifact. A green PR alone is not release proof.
