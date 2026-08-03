# tensor-grep durable campaign memory

Last updated: 2026-08-03

## Resume here

1. Work only in `.claude/worktrees/task2-tracker-truth`, branch `campaign/task2-tracker-truth`, PR
   #911. The root checkout contains unrelated staged/unstaged user work. PR #911's previous CI-proven
   head `bd07475092ec23187c45b65aa2fb8d3f2d2bfee8` passed run `30836698168`; derive the current head
   with `gh pr view 911 --json headRefOid` because recording a commit's own SHA in that commit is
   self-referential. Require a new exact-head run after every docs update.
2. Round 60 is approved. Final canonical-worktree raw SHA-256 is design
   `31D8E071F1778A59888890445A0620000548AB270EFBE11F5F2E01A70E3D862B`, implementation
   `AA64D0BA88BF98F07809065BD0E813B320C1CA7089804CDC1CD17FBB0B0826B3`. Cursor Auto found and
   closed three stale contradictions, independent TDD returned `SHIP`, Sol returned substantive `SHIP`,
   then `CONFIRMED` the status-only final hashes. Push PR #911, then start Task 2A's independent RED.
3. The nine Task-2A blockers are: protected CNG-backed receipt authority outside PATH; TxR registry
   mutation with no fallback; opened-directory identity for PATH aliases; Microsoft-root policy plus
   production thumbprints and exact offline WinTrust flags; non-breakaway Job containment; one
   no-refund ledger at every bootstrap/full/native/rg/sidecar door; inclusive combined pattern/ignore
   boundary REDs; live Actions/artifact identity re-derivation; and JUnit/Rust census cross-checking.
4. Task 2A RED work is isolated at `/home/james/.cursor/worktrees/tensor-grep/task2a-round60-red`.
   Commits `4efcad9` and `8df269d` were correctly rejected by Sol because they still used surrogate
   routes, fake/final-verdict security adapters, impossible Windows assertions, or self-attested CI
   evidence. Do not start GREEN until the amended RED receives exact-byte Sol `SHIP`. Then execute
   Tasks 2B–15 with TDD, real-venv verification, independent review, PR drain discipline,
   merged-artifact checks, and published-wheel dogfood.

## External state at the snapshot

- Public release: `v1.102.1`; PyPI install reports `tensor-grep 1.102.1`.
- `origin/main`: `8024125612d5fb42481acde34d94ad39bbaa3c3e`.
- PR #910: merged; exact PR CI run `30777042942`, 39 completed jobs, 0 failed/unfinished; merged-board
  test 7/7 passed.
- Open PRs: PR #911 only; CLEAN/mergeable and exact committed checks green at the snapshot.
- Open GitHub issues: #48 only.
- Main CI run `30793797849` for merge `8024125` completed successfully; re-query the
  newest exact main run before any next merge.
- Financial spend: none incurred or authorized.
- Local validation: docs/skill governance 93 passed; all three changed skills validate. Agent readiness
  passed 11/13, with environment-only failures: editable warmup timed out at 240s and the no-sync
  worktree CLI reported 1.102.0. The no-sync venv also lacks PyYAML for the release-asset validator.
- Cross-OS venv recovery: a WSL `uv --project /mnt/c/dev/projects/tensor-grep` probe replaced the
  Windows `.venv`. The incompatible shell was moved to
  `%LOCALAPPDATA%\Temp\tensor-grep-venv-wsl-incompatible-20260803`; Windows `uv sync --frozen`
  rebuilt the canonical venv and verified `click 8.4.2` / `tensor-grep 1.102.0`. Never point WSL `uv`
  at the Windows checkout again (AGENTS.md A60).

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
`docs/TASK_BOARD.md`, `docs/audits/2026-08-02-backlog-reconciliation.md`, and
`docs/audits/2026-08-03-ceo-backlog-update.md`. There are exactly 23 unfinished canonical rows:
10 `READY`, 5 `CEO_GATED` (four nonfinancial plus #169 financial), and 8 `DEMAND_GATED`.

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
- Green status and approval are artifact-specific: a PR head never clears newer uncommitted bytes.
- Architecture `SHIP` does not substitute for an adversarial-security `SHIP`.
- Security plans name enforceable OS/API primitives; abstract CAS, ownership, or containment labels fail.
- PATH never discovers installer authority; spelling never substitutes for opened directory identity.
- Kill-on-close containment also denies both breakaway modes and breakaway creation flags.
- Resource accounting must be installed before every front door/delegation and tested at inclusive mixed caps.
- Static manifests define required population; live receipts re-derived from CI context prove execution.
- Retry oversized review prompts on exact paragraphs; a no-verdict seat is failed, not an infinite wait.
- Discover deferred connector tools before reporting a required research provider unavailable.
- Keep WSL and Windows venv roots disjoint; `uv --project /mnt/c/...` can replace the Windows `.venv`.

Canonical detail: AGENTS.md A34–A60.
