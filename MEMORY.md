# tensor-grep durable campaign memory

Last updated: 2026-08-02

## Resume here

1. Final approved canonical campaign-plan hashes are design
   `F627B23F5881C63AE525FC7226A4FF51C1EA249DB43DB1BD8B57EDDEA4E4C994`; implementation
   `E30DCCCDC62459D28AA272CB5E251CDB92FBFC6D0BA23A312BA524AF9ED8216B`; architecture, security,
   and TDD all returned `SHIP` on this exact status-stamped pair.
2. Task 2 reconciled the tracker and reproduced #89 search plus #90 scan cross-domain WSL defects.
   Amend and re-certify one typed-path TDD program before final closeout; then execute Tasks 3–15 with worktree isolation,
   TDD, real-venv verification, independent review, PR drain discipline, merged-artifact checks, and
   published-wheel dogfood.

## External state at the snapshot

- Public release: `v1.102.1`; PyPI install reports `tensor-grep 1.102.1`.
- `origin/main`: `8024125612d5fb42481acde34d94ad39bbaa3c3e`.
- PR #910: merged; exact PR CI run `30777042942`, 39 completed jobs, 0 failed/unfinished; merged-board
  test 7/7 passed.
- Open PRs: 0.
- Open GitHub issues: #48 only.
- Main CI run `30778356638` for merge `8024125` completed successfully with 39/39 jobs; re-query the
  newest exact main run before any next merge.
- Financial spend: none incurred or authorized.
- Local validation: docs/skill governance 93 passed; all three changed skills validate. Agent readiness
  passed 11/13, with environment-only failures: editable warmup timed out at 240s and the no-sync
  worktree CLI reported 1.102.0. The no-sync venv also lacks PyYAML for the release-asset validator.

## Queue

- P0: tracker truth; #859 secure writer census/fixes; MCP surface disclosure; Rust
  and Python CPU-backend twin hardening.
- P1: edit verification; `verify-edit`; strict `edit-ready`; registry-driven refs/callers; five in-file
  language waves; six cross-file resolution waves; federated prepare service/CLI/MCP.
- Ready: #89 search receives `path_not_found` for a valid `/mnt/c/...` root; #90 scan emits a false
  clear after Windows ast-grep rejects that spelling, while the translated control finds six matches.
  Amend/review their shared typed-path program before build.
- Environment finding: `ENV-VENV-DRIFT` requires main-venv reconciliation before local CLI-version/
  release-validator receipts are trusted; it does not block #89/#90.
- Nonfinancial decision-gated (continue without asking under the current instruction): #48, #72,
  #77/F9, #131. Financial approval required before spend: #169 and any paid #255 experiment.
- Demand/research: #255, F10, DD-004, DD-006, AST DSL/C++ macro ceiling, MCP lean-default,
  RUST-REPLACE-SYMLINK,
  continuous refresh, context/session latency, token economy, call-site evidence, target-selection
  accuracy, classify provider/cache UX, cross-OS ast-grep, LSP proof-mode.

Full closed-world status and receipts:
`docs/TASK_BOARD.md` plus `docs/audits/2026-08-02-backlog-reconciliation.md`; the CEO update is an
interim narrative with a correction notice.

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
- Hash one named canonical plan artifact/method; clean-filter-equivalent worktrees can have different raw bytes.
- Validate cross-task producer/consumer order; command discovery is not a behavioral RED.
- Anchor lock creation, protected-index RMW, and bounded config reads to verified directory handles.
- Give every deferred security/compatibility behavior a stable ID, owner, threat boundary, and trigger.
- Transition `READY` to `IN_FLIGHT` inside the real numbered draft PR; close `SHIPPED` separately.

Canonical detail: AGENTS.md A34–A50.
