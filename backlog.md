# backlog.md — full dependency-mapped task list
# Generated 2026-09-03 11:20 ET from docs/TASK_BOARD.md canonical status index + orchestrator state + Zvec Parity Wave

## Legend
```
Status: SHIPPED ✓ | READY_TO_SHIP 🚀 | IN_PROGRESS 🔧 | BLOCKED ⛔ | CEO_GATED 🔑 | DEMAND_GATED 📊 | RETIRED ✗
Deps: → "required before this can start"
Opens: → "unlocked once this completes"
```

---

## READY_TO_SHIP 🚀

*(no items currently in staging; W2-c verified)*

---

## IN_PROGRESS 🔧

### HANDLER-CENSUS-W2-b — GPU backend handler census (cybert, cudf, torch)
- **Status:** READY (22 broad handlers across cybert_backend.py, cudf_backend.py, torch_backend.py)
- **AI-Doable:** YES (Census/disposition only; GPU runtime deferred per Rule A12)
- **Deps:** HANDLER-CENSUS-W2-c (completed)
- **Opens:** full backend closure (ARCH-002)

---

## SHIPPED ✓ (Recent)

### HANDLER-CENSUS-W2-c — AST, Rust, and StringZilla backend handler dispositions
- **Status:** SHIPPED (Verified on 1aee5a4 by Sonnet 5 + Codex Sol dual GO)
- **Components:** 8 handlers across `backends/ast_backend.py` (2), `backends/ast_wrapper_backend.py` (3), `backends/rust_backend.py` (2), `backends/stringzilla_backend.py` (1) dispositioned in `docs/audits/2026-08-20-handler-dispositions.json` (155 total); `_EXPLICIT_AUDITED_MODULES` extended in `tests/unit/test_handler_dispositions.py`.
- **Verification:** 11/11 tests pass in `test_handler_dispositions.py`, 2/2 tests pass in `test_silent_failure_hardening.py`, ruff/mypy clean.

### HANDLER-CENSUS-W2-a — cpu_backend + ripgrep handler hardening
- **Status:** SHIPPED (Released in v1.114.1)
- **Components:** 17 handlers dispositioned; decode/search exception separation in `cpu_backend.py`.
- **Verification:** 11/11 tests pass in `test_handler_dispositions.py`, ruff/mypy clean.

### ZVEC-PARITY-AGENT-ENHANCE — AST Container Enrichment & Multi-Agent MCP Installer
- **Status:** SHIPPED (Released in v1.114.0)
- **Components:** `--enrich-ast` container enrichment + multi-agent installer (`tg install`/`tg uninstall` for claude, cursor, codex, opencode, qwen).
- **Verification:** 22/22 unit tests pass, 68/68 routing tests pass, ruff/mypy clean.


---

## BLOCKED ⛔

### #89 — WSL path-domain `path_not_found`
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment-blocked)
- **Blocker Receipt:** Real WSL host required (`path_not_found` on existing `/mnt/c`); PR #966 closed as wrong scope on 2026-08-20. Needs a dedicated WSL CI runner or hardware testbed.
- **Deps:** Real WSL host environment
- **Opens:** #90, F8 partial

### #90 — WSL raw-path scan `matched_rules=0`
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment-blocked)
- **Blocker Receipt:** WSL raw-path scan reports `matched_rules=0` while translated-path control reports `total_matches=6`. Doctor half shipped in PR #571; scan half waits on typed-path + real WSL CI runner.
- **Deps:** #89 (shares real WSL host environment)
- **Opens:** nothing current

### F5 — Edit-ready Steps 3–5 (Rust/e2e)
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment/infra-blocked)
- **Blocker Receipt:** Touches `rust_core/**` + `tests/e2e/**`; shared-box cargo/e2e ban (Operating Rule A12) prohibits cold full-suite local native cargo builds to prevent desktop resource starvation. Step 2 shipped in PR #943.
- **Deps:** CI/cloud runner unblock (CEO or dedicated cloud builder)
- **Opens:** F6 native half, F8 workspace Rust

### F6 — Edit verification (mixed disposition)
- **Status:** BLOCKED (Partial — Python slices buildable)
- **AI-Doable:** PARTIAL (Python half AI-doable; native half blocked)
- **Blocker (native half):** Rust/e2e shared-box ban.
- **Buildable today:** Python/schema/evidence-signing S1 slices → need scoped plan + A3 gate.
- **Deps (Python half):** Task 2C for MCP ordering.
- **Deps (native half):** F5 / CI unblock.
- **Opens:** MCP-SURFACE (Task 4 sequenced after 2C which is in F6).

### F8 — Workspace program (Tasks 12–13)
- **Status:** BLOCKED
- **AI-Doable:** PARTIAL
- **Blocker Receipt:** `path_domain.rs` absent on origin/main; requires an architectural design naming workspace APIs before Rust implementation can begin.
- **Deps:** Design pass; F5 (Rust cargo ban on shared box).
- **Opens:** future workspace search slices.

### MCP-SURFACE — Task 4 MCP surface disclosure
- **Status:** BLOCKED
- **AI-Doable:** YES (once sequence unblocks)
- **Blocker Receipt:** Strictly sequenced after Task 2C (F6 Python chain). Live `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` at `src/tensor_grep/cli/mcp_server.py:188`.
- **Deps:** F6 Python slices (Task 2C completes).
- **Opens:** MCP-LEAN-DEFAULT (DEMAND_GATED).

---

## CEO_GATED 🔑

### #72 — New public benchmark claim
- **Status:** CEO_GATED
- **AI-Doable:** NO (Policy-gated)
- **Blocker Receipt:** CEO approval required for any new public speed or throughput claim.
- **Deps:** CEO explicit approval
- **Opens:** public benchmark docs & marketing materials

### #77 — Ledger enforcement scope
- **Status:** CEO_GATED
- **AI-Doable:** NO (Policy-gated)
- **Blocker Receipt:** CEO policy decision required on F9 / ledger scope.
- **Deps:** CEO explicit directive
- **Opens:** F9 ledger enforcement

### #131 — GPU-flavor native asset publish
- **Status:** CEO_GATED
- **AI-Doable:** NO (Financial/Policy-gated)
- **Blocker Receipt:** CEO approval required to build and publish separate GPU-flavored binary wheels; gated on #169.
- **Deps:** #169 (parent financial spend gate)
- **Opens:** GPU benchmark claims (#72 partial)

### #169 — Physical GPU proof / spend
- **Status:** CEO_GATED — **Only mandatory financial stop**
- **AI-Doable:** NO (Spend-gated)
- **Blocker Receipt:** Requires CEO approval for physical hardware or cloud GPU spend (RunPod, Lambda, etc.).
- **Deps:** CEO spend authorization ($)
- **Opens:** #131, Phase 2 GPU CI runner

---

## DEMAND_GATED 📊

### #255 — Many-pattern dedup parity experiment
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when demand occurs)
- **Trigger Condition:** Named 100+-pattern user or approved compression/native investment. Standing council verdict: LEAVE (2026-08-14).
- **Deps:** Customer demand receipt
- **Opens:** nothing on current board

### DD-006 — Concurrent daemon DoS hardening (PERF + HONESTY build)
- **Status:** DEMAND_GATED
- **AI-Doable:** YES
- **Trigger Condition:** Demand condition SATISFIED (2026-08-14 bounded probe); design PR #1015 merged (`0710219`). Awaiting CEO "build go" authorization.
- **Deps:** CEO authorization → TDD + A3 adversarial gate
- **Opens:** MCP-LEAN-DEFAULT (sequencing)

### AST-DSL-PARITY — Full structural DSL parity
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when demand occurs)
- **Trigger Condition:** Concrete consumer blocked on ast-grep metavariable parity. Council verdict: LEAVE (2026-08-14).
- **Deps:** Consumer requirement
- **Opens:** advanced structural query support

### MCP-LEAN-DEFAULT — Lean MCP surface by default
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when sequenced)
- **Trigger Condition:** Client demand and compatibility evidence for changing default MCP surface (up to 85% token savings).
- **Deps:** MCP-SURFACE (Task 4)
- **Opens:** token-efficiency gains across LLM harnesses

### CONTINUOUS-REFRESH — Warm search-index daemon
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (scoping pass)
- **Trigger Condition:** Approved scoping/design pass for warm search-index service.
- **Deps:** Scoping authorization
- **Opens:** sub-millisecond warm search latency

### RUST-REPLACE-TOCTOU — Residual TOCTOU races in replace_in_place
- **Status:** DEMAND_GATED
- **AI-Doable:** YES
- **Trigger Condition:** Characterization pin in `backend_cpu.rs` inverting.
- **Deps:** RUST-REPLACE-SYMLINK ✓ (shipped in PR #1010)
- **Opens:** full replace_in_place safety across directory swap windows

---

## SHIPPED ✓

### #36 — Skill drift audit
- **Shipped:** PR #903
- **Opens:** Maintenance baseline

### #37 — Grammar-dependent Windows test
- **Shipped:** PR #908
- **Opens:** CI reliability on Windows

### #109 — CUDA implicit-walk ceiling
- **Shipped:** PR #605
- **Opens:** GPU search stability

### #859 — Task 3 AST writer census + publication fix
- **Shipped:** PR #913, #918, #920 (`211d850c`)
- **Opens:** Class-level AST writer ratchets

### F7 — Language registry + cross-file resolution (Tasks 10–11)
- **Shipped:** PR #950, #952, #955, #957, #963 (`9f854d49`)
- **Opens:** Multi-language symbol navigation (Java, C#, PHP, C, C++)

### CPU-BACKEND — Task 5 Rust/Python backend hardening
- **Shipped:** PR #923, #925, #963 (`f29c9484`)
- **Opens:** Correct fail-closed semantics in CPU fallback

### REF-CALL-REGISTRY — Task 9 registry-driven refs/callers
- **Shipped:** PR #915, #940, #963 (`3dbe85b1`)
- **Opens:** Semantic code navigation across references

### RUST-REPLACE-SYMLINK — Fail-closed symlink/junction guard
- **Shipped:** PR #1010 (`d31a051f`, v1.110.16)
- **Opens:** RUST-REPLACE-TOCTOU

---

## RETIRED ✗

### #22 — Exit code semantics
- **Retired:** exit 0/1/2 contract permanently locked; `gpu_request_unhonoured` is in-band.

### F2 — Anonymous-agent compatibility sentinel
- **Retired:** Deliberately retained for backward compatibility.

### #48 — Native front-door rewrite
- **Retired:** Closed "not planned" 2026-08-24 applying standing 5/5 council verdict.

### F10 — MaxSim / late rerank
- **Retired:** Negative on golden set (ndcg@10 0.068 vs 0.305 RRF); model capacity limitation.

### DD-004 — Typed BackendExecutionError boundary
- **Retired:** INFO/WEAKENED loud `RuntimeError` re-raise at `cpu_backend.py:811` is fail-closed.

---

## Full Dependency Graph (Text & Topological Map)

```
========================================================================================
                                DEPENDENCY MAP
========================================================================================

[READY TO SHIP]
  ZVEC-PARITY-AGENT-ENHANCE (Verified Green 22/22, Sol SHIP) ──► Production v1.114.0

[IN PROGRESS]
  PR #1124 (Merged) ──► HANDLER-CENSUS-W2-a ──► Sol AUDIT_CLEAR ──► HANDLER-CENSUS-W2-b

[CEO GATES & FINANCIAL SPEND]
  #169 ($ Financial Stop) ────┬──► #131 (GPU Native Assets) ──► #72 (GPU Benchmark Claims)
                              └──► Phase 2 GPU CI Cloud Runner

[ENVIRONMENT BLOCKS]
  Real WSL Host ─────────────► #89 (WSL Path Domain) ──► #90 (WSL Raw-Path Scan)
  CI/Cloud Runner (Non-local) ► F5 (Rust/e2e Steps 3-5)
                                     │
                                     ├──► F6 Native Half
                                     └──► F8 Workspace Rust

[MCP & EDIT SEQUENCING]
  F6 Python Slices (Task 2C) ──► MCP-SURFACE (Task 4) ──► MCP-LEAN-DEFAULT (Demand)

[DEMAND TRIGGERS]
  CEO Build Authorization ────► DD-006 (Concurrent Daemon DoS Hardening)
  RUST-REPLACE-SYMLINK (✓) ───► RUST-REPLACE-TOCTOU (Pin Inversion Acceptance)
========================================================================================
```

*All rows reconciled mechanically from `docs/TASK_BOARD.md` + live git working tree.*
