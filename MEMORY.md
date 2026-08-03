# tensor-grep durable campaign memory

Last updated: 2026-08-03 (continuation)

## Resume here

1. Work only in `.claude/worktrees/task2-tracker-truth`, branch `campaign/task2-tracker-truth`, PR
   #911. The root checkout contains unrelated staged/unstaged user work. At last external observation,
   PR #911 head `01f276fa7c0d3d0e04fdb5feae78c29c1b194773` was CLEAN/MERGEABLE with CI
   `30842604458`, security `30842604251`, CodeQL success. Derive the live head with
   `gh pr view 911 --json headRefOid` — do not embed a commit’s own green verdict inside that commit.
   Human may merge once the head being merged has exact completed green evidence.
2. Round 60 remains approved. Final canonical-worktree raw SHA-256 is design
   `31D8E071F1778A59888890445A0620000548AB270EFBE11F5F2E01A70E3D862B`, implementation
   `AA64D0BA88BF98F07809065BD0E813B320C1CA7089804CDC1CD17FBB0B0826B3`.
3. Product healthy at `v1.102.1`; planning PR merge-ready on last observed exact head; backlog not
   done; Task 2A correctly blocked. Closed world: 28 rows / 23 unfinished = 10 READY, 5 CEO_GATED,
   8 DEMAND_GATED. Research recommendations are not silent reclassification. No question for
   nonfinancial gates; #169 is the only mandatory financial stop. No spend.
4. Task 2A RED is isolated local-only at exact SHA `6367614960327b1a4e00301c8bfdb9b2e4bb453e`
   (branch/HEAD match, unpushed, no Actions run, no GREEN). Sol exact-byte verdict is `FIX-FIRST`
   with 10 HIGH blockers. Older rejects `4efcad9` / `8df269d` are historical only. After #911
   merged-base proof: Cursor repairs the ten blockers → Sol repeats until `SHIP` → push draft →
   real Windows CI. Do not call Task 2A merge-ready.

## External state at the snapshot

- Public release: `v1.102.1`; PyPI install reports `tensor-grep 1.102.1`.
- `origin/main`: `8024125612d5fb42481acde34d94ad39bbaa3c3e`.
- PR #910: merged; exact PR CI run `30777042942`, 39 completed jobs, 0 failed/unfinished; merged-board
  test 7/7 passed.
- Open PRs: PR #911 only; last observed exact head CLEAN/MERGEABLE + green (CI/security/CodeQL).
- Open GitHub issues: #48 only.
- Main CI run `30793797849` for merge `8024125` completed successfully; re-query the
  newest exact main run before any next merge.
- Financial spend: none incurred or authorized.
- Local Task 2A RED replay: manifest 157 unique nodes (148 Python + 9 Rust); jobs 95 Python / 62
  native; native 44F/9P; installer 13F/18P/4S; ledger 32F/3P; win32 2F/11P/12S; CI governance 6P;
  Ruff + preview format clean. Failures are RED evidence, not GREEN clearance.
- Cross-OS venv recovery: a WSL `uv --project /mnt/c/dev/projects/tensor-grep` probe replaced the
  Windows `.venv`. The incompatible shell was moved to
  `%LOCALAPPDATA%\Temp\tensor-grep-venv-wsl-incompatible-20260803`; Windows `uv sync --frozen`
  rebuilt the canonical venv. Never point WSL `uv` at the Windows checkout again (AGENTS.md A60).

## Queue

- P0 after Task 2A: #859 secure writer census/fixes; MCP surface disclosure; Rust/Python CPU-backend
  twin hardening.
- P1: edit verification; `verify-edit`; strict `edit-ready`; registry-driven refs/callers; five in-file
  language waves; six cross-file resolution waves; federated prepare service/CLI/MCP.
- Ready: #89/#90 remain `READY` with reproduced WSL path-domain defects; Task 2A RED must reach Sol
  `SHIP` before GREEN product work.
- Nonfinancial decision-gated (continue without asking; recommendations only): #48 accept shipped
  hybrid native+sidecar; #72 HOLD public 7.5x pending zero-spend fresh six-repo/180-task quality gate;
  #77 local opt-in advisory only; #131 optional experimental NVIDIA asset, CPU default, no speed claim.
  Financial: #169 only.
- Demand/research: #255; F10 (census then maybe retire); DD-004 (likely retire + bank typed-boundary
  rule); DD-006; AST DSL; MCP lean-default; RUST-REPLACE-SYMLINK; continuous refresh.

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
- **A61** Behavioral RED pins the exact expected reason; crash/import/panic/setup errors are not RED.
- **A62** Route/start evidence comes from the actual producer/constructor and test-owned OS/raw
  evidence — never a hardcoded bool or production self-attest hook.
- **A63** Containment proof authenticates writer/client provenance and proves alive-before →
  dead-after plus cleanup — not Event/EOF/PID text.
- **A64** Crypto negative proof uses a valid API operation, exact refusal class, and an
  exportable/trusted positive control.
- **A65** Security grammar validates full sections/types/flags/effective authority; rejects unknown
  and inherit-only — not substring principals.
- **A66** Resource-owning protocols name close primitives and prove exact-once reverse cleanup on
  success, BaseException, and cleanup failure while preserving the primary error.
- **A67** RED scaffolds cannot enable partial public behavior or unbounded work before the guard.
- **A68** Immutable-SHA CI clearance needs a real run, expected per-node outcomes, raw artifacts, and
  exact population; no run is no clearance.

Canonical detail: AGENTS.md A34–A68.
