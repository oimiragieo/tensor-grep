# tensor-grep durable campaign memory

Last updated: 2026-08-02

## Resume here

1. Plan approval is complete. Final approved hashes: design
   `72A0C4A8EB82EEC6DC0121C44D1EF142CCB974C23CC7ED990D7BC15484B1D7E1`; implementation
   `1B4F801C7CB2D1A1952F8E5279C4501C84A465D7892C08C352249ADB21AC6071`; architecture, security,
   and TDD seats all returned `SHIP`.
2. Execute Tasks 2–15 with worktree isolation, TDD, real-venv verification, independent review,
   PR drain discipline, merged-artifact checks, and published-wheel dogfood.

## External state at the snapshot

- Public release: `v1.102.1`; PyPI install reports `tensor-grep 1.102.1`.
- `origin/main`: `8024125612d5fb42481acde34d94ad39bbaa3c3e`.
- PR #910: merged; exact PR CI run `30777042942`, 39 completed jobs, 0 failed/unfinished; merged-board
  test 7/7 passed.
- Open PRs: 0.
- Open GitHub issues: #48 only.
- Main CI run `30778356638` for merge `8024125` was still in progress at the last check (30 jobs
  visible, 1 unfinished); re-query the exact run before any next merge.
- Financial spend: none incurred or authorized.
- Local validation: docs/skill governance 93 passed; all three changed skills validate. Agent readiness
  passed 11/13, with environment-only failures: editable warmup timed out at 240s and the no-sync
  worktree CLI reported 1.102.0. The no-sync venv also lacks PyYAML for the release-asset validator.

## Queue

- P0: plan convergence; tracker truth; #859 secure writer census/fixes; MCP surface disclosure; Rust
  and Python CPU-backend twin hardening.
- P1: edit verification; `verify-edit`; strict `edit-ready`; registry-driven refs/callers; five in-file
  language waves; six cross-file resolution waves; federated prepare service/CLI/MCP.
- Blocked: #89 requires an available WSL/Linux environment; `ENV-VENV-DRIFT` requires main-venv
  reconciliation before local CLI-version/release-validator receipts are trusted.
- CEO/financial: #48, #72, #77/F9, #131, #169.
- Demand/research: #255, F10, DD-004, DD-006, AST DSL/C++ macro ceiling, MCP lean-default,
  continuous refresh, context/session latency, token economy, call-site evidence, target-selection
  accuracy, classify provider/cache UX, cross-OS ast-grep, LSP proof-mode.

Full closed-world status and receipts:
`docs/audits/2026-08-02-ceo-backlog-update.md`.

## Retained laws from this campaign

- PR prose/metadata is part of the artifact and must be re-reviewed after scope changes.
- Plan approval expires when a live-code premise changes.
- A site test is not a class census; include generated source, aliases, shadows, and mutations.
- Preserve leaf identity and anchor both directory creation and publication against parent swaps.
- Search twins for every retired defect shape.
- No internal caller does not authorize deletion of a public API.
- Preserve mixed outcomes instead of flattening them into “shipped.”
- Producer/consumer dogfood records both exits and must not dirty the subject under verification.
- Exact CI proof includes run ID, head SHA, stable job population, and zero unfinished/failing jobs.
- Attribute PR head, squash merge, main CI head, and release commit separately.

Canonical detail: AGENTS.md A34–A45.
