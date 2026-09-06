# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized/historical work ledger.** Kept in sync with the CLI task store (`TaskUpdate`);
> GitHub (`gh pr list`) is the source of truth for PRs. The machine-parsed canonical status index in
> `docs/TASK_BOARD.md` is the live-state view; use the dated
> closed-world audit linked below. **CEO status** enumerates every live disposition—active, blocked,
> nonfinancial decision-gated, financial/spend-gated, demand/research-gated, and mixed/terminal
> corrections—not merely SHIPPING or P0/P1. Update whenever a PR opens/merges or the queue changes.
> Task-store IDs (`#NNN`) are cross-referenced.
> **Current closed-world CEO snapshot: 2026-08-13, release `v1.110.16`, merged main `8f7db83`.** Backlog-closeout campaign W1-W4: W1 premise receipt (PR #1008), W2 A101 probe retry (PR #1009, v1.110.15), W3 RUST-REPLACE-SYMLINK threat model + guard (PR #1010, v1.110.16, Merged SHA d31a051), W4 Task 2A repair round 1 receipt on #966. RUST-REPLACE-TOCTOU row filed. See docs/audits/2026-08-13-ceo-backlog-update.md for the live disposition list.
> Live disposition is the canonical index in `docs/TASK_BOARD.md` (28 rows / 17 unfinished =
> **1 READY** (RUST-REPLACE-SYMLINK, design-council-first), 6 BLOCKED, 0 IN_FLIGHT, 5 CEO_GATED,
> 5 DEMAND_GATED). Campaign receipts: `docs/plans/2026-08-12-backlog-closeout-campaign.md` +
> `docs/audits/2026-08-12-stale-branch-reconciliation.md`. Prior packets:
> `docs/audits/2026-08-11-ceo-backlog-update.md` (A83–A96),
> `docs/audits/2026-08-06-pm-ceo-backlog-update.md` (A77-A82 / pre-stamp READY):
> `docs/audits/2026-08-06-ceo-backlog-update.md`. Task 2A ADVANCED 2026-08-12 (#966
> CONFLICTING→MERGEABLE, first Actions evidence chain, five repair rounds) and remains correctly
> blocked (Sol SHIP + Windows census outstanding). No spend; #169 only financial stop.
> Historical header text below this block may still describe older READY counts — trust the
> canonical index + PM CEO packet, not stale prose in this ledger's opening paragraph.
> Recovered local-environment incident (historical): `ENV-VENV-DRIFT` occurred when a WSL `uv`
> probe replaced the canonical Windows `.venv`. The incompatible environment was moved aside and
> Windows `uv sync --frozen` rebuilt and verified the canonical environment; this is not an active
> backlog row or blocker. A60 is the prevention rule: never point WSL `uv` at the Windows checkout,
> and treat worktree-local no-sync output as a hypothesis until it is replayed in the real main venv
> or CI. Current validation belongs in the dated audit/session handoff, not this historical ledger.
> Round-18 thinktank findings are retained as plan-owned work, not lost review prose: prerequisite
> prepare-service extraction before edit verification; behavior-specific Python/native/evidence REDs;
> a fully typed workspace-prepare schema; handle-relative first-use claims-fence creation plus
> `flock`/`LockFileEx` held across RMW; bounded/confined project-config readers; real-PR tracker
> transitions; and new canonical demand row `RUST-REPLACE-SYMLINK` for the public Rust direct-file
> leaf-symlink compatibility/security decision. See the dated CEO audit and Tasks 2–15 for owners,
> triggers, and closure tests. None has been implemented merely by amending the plan.
> Round 60 remains exact-hash approved (`31D8E071...3D862B` / `AA64D0BA...0826B3`). Older PR #911
> head `01f276fa7c0d3d0e04fdb5feae78c29c1b194773` was green, but docs head
> `fb99d2bce4ba722b724212282158bf6616b1ade2` lost clearance when security run `30857841901`
> found four fixable `aiohttp`/`cryptography` advisories (CodeQL `30857839262` passed). The successor
> raises floors to `aiohttp>=3.14.3` / `cryptography>=50.0.0` and regenerates the lock; require new
> exact-head CI/security/CodeQL before merge. Task 2A RED is local only at
> `6367614960327b1a4e00301c8bfdb9b2e4bb453e` (unpushed, no Actions run, Sol `FIX-FIRST` / 10 HIGH).
> No authorized GREEN phase has started; Sol found accidental public behavior inside the RED scaffold,
> which must be removed. Pause #911 at merge only after re-clearance, then repair RED → Sol `SHIP` → real Windows CI
> before Task 3. Research recommendations (#48/#72/#77/#131/DD-004/F10) are not silent reclassification.
> No question is asked for nonfinancial gates; #169 remains the only mandatory financial stop.
>



## STRATEGIC (2026-09-04): 2026 Competitive Analysis & Strategic Updates Roadmap

Competitive landscape audit against mid-2026 codebase intelligence and agent context tooling (`Gortex`, `GitNexus`, `Serena`, `GrepAI`, `ripgrep`, `ast-grep`, `Claude Code` native agentic search).

### 1. Landscape Diagnosis & Positioning
- **The Market Shift:** Industry consensus in 2026 has moved away from pure vector RAG (high token tax, stale vector indices, hallucinated relevance) and away from raw iterative CLI tool loops (agents calling grep/cat 10+ times) toward **Agent-Native Structural & Codebase Intelligence Layers**.
- **Where `tg` Leads:**
  - **4-in-1 Edit Readiness Capsule (`tg prepare`):** Bundles primary target, confidence score, blast-radius floor with graph provenance, detected validation commands, and machine-branchable human escalation flag (`ask_user_before_editing`). No surveyed competitor (`Gortex`, `GitNexus`, `CodeGraph`, `Aider`) bundles all four in a single call.
  - **Section 0 Completeness Contract:** Exit codes 0/1/2 strictly agree with payload; closed vocabulary (`scan_limit`, `deadline`, `timeout`, `unreadable_path`); fail-closed `budget_remediable` verdict. No other tool provides this contract to protect agents from deleting code on empty truncated scans.
  - **Zero-Setup Local Density:** Local CPU BM25 and lightweight ~65MB model2vec (`potion-code-16M`) via `tg find` without external vector DB dependencies.
- **Where `tg` Lags:**
  - **Language Depth:** `tg` has 5 deep parser-backed languages (Python, Go, JS, TS, Rust) and 5 defs-only foundational languages (C, C++, C#, Java, PHP). Gortex claims ~30 bespoke languages with resolved call edges, and Serena wraps 40+ via LSP.
  - **Semantic Disconnect in Agent Capsules:** `tg prepare` and `tg agent` do not leverage `retrieval_dense` or `retrieval_fusion`, leaving them vulnerable to natural language vocabulary mismatch.
  - **Git Diff / PR Impact Analysis:** Gortex (`pr_risk`, `get_pr_impact`) and GitNexus offer diff-level blast radius; `tg` currently has symbol-level blast radius only.

---

### 2. Prioritized Strategic Updates (P0 – P4)

- **[x] P0 — Fuse Semantic Dense Retrieval into `tg prepare` / `tg agent`**
  - **Objective:** Eliminate task-description vocabulary mismatch without mandatory GPU or network dependencies.
  - **Scope:** Wire `retrieval_dense` / `retrieval_fusion` from `tg find` as an optional fallback or hybrid signal in `build_agent_capsule` / `prepare` when lexical term matching yields low confidence (<0.6).
  - **Acceptance:** Natural-language queries with mismatched vocabulary (e.g. "sales surcharge calculation" for `compute_tax`) successfully locate target symbol; exits 0 with high confidence; falls back cleanly to lexical-only if dense extra is not installed.

- **[x] P1 — Git Diff-Aware Blast Radius (`tg diff-impact` / `tg pr-risk`)**
  - **Objective:** Compete directly with Gortex `pr_risk` and GitNexus impact analysis in automated CI/PR gates.
  - **Scope:** Add `tg diff-impact [REF]` (e.g., `HEAD~1`, `--staged`) that parses modified symbols across the diff, computes union blast-radius floor, identifies affected downstream tests, and outputs a structured review readiness risk score.
  - **Acceptance:** Outputs JSON with `changed_symbols`, `affected_callers`, `impacted_tests`, `risk_tier`; adheres to Section 0 completeness contract with deadline/token bounds.
  - **Receipt:** Shipped in PR #1128, merged SHA `7d2baa5`, released in `v1.116.0` (run `33995069360`).

- **[x] P2 — Deepen Language Coverage in `LANGUAGE_REGISTRY` (5 -> 10 Deep Languages)**
  - **Objective:** Close the language depth gap against Gortex (~30) and Serena (40+).
  - **Scope:** Upgrade the 5 foundational languages (Java, C#, C, C++, PHP) from regex caller heuristics (`_regex_references_and_calls`) to full tree-sitter AST-verified references and callers.
  - **Acceptance:** `_symbol_navigation_descriptor()` reports 10 parser-backed languages; zero regex fallback regressions on cross-file caller queries in test matrix.
  - **Receipt:** Merged PR #1129, squash SHA `c762b1c`, all CI green. Worktree + branch cleaned up.

- **[x] S1/S5 — Fail-Closed Edit Tickets & Verify-Edit Service (`EditReadyTicketV1`)**
  - **Objective:** Contract-driven workspace edit validation preventing silent hallucinated agent edits.
  - **Scope:** Working tree fingerprinting, sha256 pre/post assertions, bounded hunk verification.
  - **Receipt:** Merged PR #1133, squash SHA `a4e2d71`, all CI green. Worktree + branch cleaned up.

- **[ ] S2 — Registration-Aware Polyglot Diff Impact**
  - **Objective:** Extend `diff-impact` symbol mapping to polyglot Go and Rust source trees.
  - **Scope:** AST symbol resolution on modified diff hunks.
  - **Status:** PR #1130 open (`3ece043`); CI green except one `test-python (windows-latest, py3.11)` job cancelled by cross-PR concurrency contention — rerun dispatched, run `33999557841` (job `101403039873`) in progress.

- **[x] S3 — Machine Protocol `next_action` & Budget Envelopes**
  - **Objective:** Structured branch advice in `tg prepare` output payload for agent runtimes.
  - **Scope:** Explicit remediation instructions, token and time budget ceiling envelopes.
  - **Receipt:** Merged PR #1132, squash SHA `3e56c22`, all CI green. Worktree + branch cleaned up.

- **[ ] S4 — Warm Session Prepare & Resume Contracts**
  - **Objective:** Rapid cached agent context restoration across sequential edit turns.
  - **Scope:** `tg session-prepare` and `tg session-resume` CLI bindings with decoupled service architecture (`main.py` <= 13,523 ratchet).
  - **Status:** PR #1131 fixed (`b317bff`): extracted `session_prepare_cmd`/`session_resume_cmd` into new `src/tensor_grep/cli/session_resume_service.py`, `main.py` now 13522 lines (1 under 13523 baseline). Verified: real import smoke test passes, `test_file_size_budget.py` + session test suite green (152 passed, 1 skipped). CI rerun in progress on the new push; merge once green.

- **[ ] S6 — Semantic Defaults & `--why-ranked` Explanations in `tg find`**
  - **Objective:** Match scoring transparency and explicit installation capability envelopes.
  - **Scope:** Expose breakdown of BM25 + dense fusion scores in find CLI payload.
  - **Status:** PR #1134 fixed (`a5b774d`): extracted `build_why_ranked_reasons`/`route_labels` helpers into `src/tensor_grep/core/reranker.py`, `main.py` now exactly 13523 lines (matches baseline). Verified: real import smoke test passes, ratchet + why_ranked/find tests green (163 passed). CI rerun in progress; merge once green.

- **[ ] P3 — Standardize MCP Incompleteness Protocol Envelope**
  - **Objective:** Establish `tg`'s fail-closed incompleteness contract as the gold standard across all MCP clients.
  - **Scope:** Add a unified, additive JSON field `incomplete: {"status": bool, "cause": str, "budget_remediable": bool}` across all tool responses in `mcp_server.py` alongside existing surface-specific fields.
  - **Acceptance:** Validated across all 58 MCP tool endpoints; client agents in Cursor, Windsurf, and Claude Code can inspect one consistent object to decide whether to retry with increased budget.
  - **Status:** Dispatched to background developer agent in new worktree `tensor-grep-p3-mcp-envelope` (branch `feat/p3-mcp-incompleteness-envelope`, off `main@203c591`), TDD-first, no PR yet — 2026-09-05.

- **[ ] P4 — Front-Door Positioning & Dynamic Language Table Realignment**
  - **Objective:** Position `tg` as the AI agent edit-readiness layer rather than a cold grep speed comparator.
  - **Scope:** Update `README.md` and `docs/tool_comparison.md` to lead with `tg prepare` and the 4-element comparison table; demote cold grep speed benchmarks to an engine appendix; generate the published language tier table dynamically from `LANGUAGE_REGISTRY` to prevent documentation rot.
  - **Acceptance:** `README.md` hero section features `tg prepare`; zero hardcoded language count drift against `LANGUAGE_REGISTRY`.

- **[x] P5 — Public Repository Cleanliness & Agent Scratch Partitioning (Enterprise OS Parity)**
  - **Objective:** Match top-tier enterprise open-source repositories (e.g. Alibaba `open-code-review`, `zvec`) by untracking and ignoring internal agent orchestration files from public view.
  - **Scope:** Add `.build/`, `.wayfinder/`, `.orchestrator/`, and `MEMORY.md` to `.gitignore`. Untrack them from the git tree (`git rm -r --cached`). Maintain all standard open-source collaboration infrastructure (`.github/` issue templates, actions, workflows, `docs/`, `src/`, `tests/`).
  - **Acceptance:** Public GitHub root displays only production-grade code, tests, docs, and standard `.github/` directories; `git status` clean.

- **[x] P6 — Merge PR #1125 & Drain Pipeline (SEC-007 Wire Error Sanitization)**
  - **Objective:** Close out SEC-007 vulnerability by sanitizing raw exceptions across all 58 MCP tool endpoints and hardening AST ratchets.
  - **Scope:** Await completion of CI run `33898390695` (which addresses the Route A `collect_device_inventory` bare-call ratchet fix), squash-merge PR #1125 into `main`, and verify published release pipeline.
  - **Acceptance:** Zero raw tracebacks or secret paths leak over MCP JSON-RPC; AST closed-world ratchet passes on all 54 authorized sites.

- **[ ] P7 — Pre-computed Persistent AstGrep Structural Rule Cache**
  - **Objective:** Accelerate repeated AST pattern queries across large multi-language repos.
  - **Scope:** Cache parsed AST rule representations and structural fingerprints in `.tensor-grep/ast_cache/` with mtime/hash validation, eliminating redundant tree-sitter parse overhead during multi-step agent edit loops.
  - **Acceptance:** Second invocation of identical AST query executes in <5ms; zero cache invalidation desyncs on modified files.

- **[ ] P8 — Cross-File Import-Graph Cycle & Dead-Code Detector (`tg graph --dead-code`)**
  - **Objective:** Enable agents to find dead code and cyclic imports during refactoring without external linters.
  - **Scope:** Traverse the symbol dependency and importer graphs constructed by `repo_map.py` to identify unreferenced symbols and circular dependencies across all 10 parser-backed languages.
  - **Acceptance:** Returns structured JSON with `unreferenced_symbols` and `import_cycles` within Section 0 bounded budgets.

- **[ ] P9 — Memory-Resident Watcher & Cache Daemon for Agent Multi-Turn Loops (`tg daemon --watch`)**
  - **Objective:** Eliminate repetitive cold-scan and AST parse latency across consecutive agent edit rounds.
  - **Scope:** Provide a lightweight background watcher service holding parsed AST and symbol tables in memory, invalidating only touched files on filesystem events.
  - **Seat & Cost:** Opus 5 design pass (Claude Max $200/mo flat plan) -> Sonnet 5 build (Droid Plus $100/mo flat plan); $0.00 marginal overage.
  - **Acceptance:** Turnaround time for `tg prepare` drops from ~2.5s to <150ms on warm multi-turn agent turns; fail-closed fallback if daemon crashes.

- **[ ] P10 — Verified Test-Execution Evidence Enclave (`tg verify --enforce-evidence`)**
  - **Objective:** Guarantee that agent claims of passing tests are backed by cryptographically verifiable execution hashes before git commit.
  - **Scope:** Extend `tg evidence` to execute designated test commands in an isolated subprocess, capture signed execution metadata, and output a tamper-evident `.evidence.json` receipt.
  - **Seat & Cost:** Codex Pro $200/mo flat plan; deterministic subshell harness; $0.00 marginal overage.
  - **Acceptance:** Deterministic verification gate; prevents "false-green" agent completion claims.

- **[ ] P11 — CI PR Blast-Radius & Risk Gate GitHub Action (`tg action pr-gate`)**
  - **Objective:** Gate pull requests and pre-commit hooks on transitive downstream impact and review risk, competing directly with `ehermanson/blast-radius` and Gortex `pr_risk`.
  - **Scope:** Package an official GitHub Action and pre-commit hook runner wrapping `tg diff-impact` that evaluates PR diffs against configurable risk thresholds (e.g. `--fail-threshold 50`, `--fail-on-risk risky`), posting an automated Mermaid caller-graph summary comment on PRs.
  - **Seat & Cost:** Sonnet 5 build (Droid Plus $100/mo flat plan) -> Opus 5 review (Claude Max $200/mo); $0.00 marginal overage.
  - **Acceptance:** Exits 0 on acceptable changes, exit 2 on exceeded blast-radius threshold; renders interactive Markdown/Mermaid dependency trees on GitHub PR comments.

- **[ ] P12 — Zero-Shot Semantic Re-ranking with Model2Vec Onnx Quantization (`tg find --dense-fast`)**
  - **Objective:** Cut semantic embedding latency from ~80ms to <10ms for multi-symbol queries on CPU.
  - **Scope:** Distill and export the default `potion-code-16M` model into an ONNX-runtime int8 format integrated with native SIMD/AVX-512 extensions in `rust_core`, bypassing Python interpreter overhead during dense fusion passes.
  - **Seat & Cost:** Sonnet 5 build (Droid Plus $100/mo) -> Fable 5.1 audit; $0.00 marginal overage.
  - **Acceptance:** 8x faster embedding vector inference; zero quality degradation on `find_realquery_golden.jsonl` benchmark suite.



## OPEN (2026-08-23): the two governance docs have no size gate, and both are now very large

Measured at the end of a session that appended heavily to both:

| file | size | added today |
|---|---:|---:|
| `AGENTS.md` | **368 KB** | +69 lines |
| `docs/BACKLOG.md` | **331 KB** | +371 lines |
| `docs/TASK_BOARD.md` | 57 KB | +8 |
| `CLAUDE.md` | 25 KB | +6 |

**Nothing gates this.** `scripts/file_size_budget.py` covers source files only — 0 mentions of
`AGENTS.md`, `BACKLOG.md` or `TASK_BOARD.md` — and no doc-size check exists anywhere in
`scripts/`.

That is the same failure class this session hit in `MEMORY.md`, which DID have a limit and so
announced itself: an append-only document grows until readers silently receive a truncated view,
and a truncated governance doc is worse than a missing one because the reader believes they have
the whole thing. `MEMORY.md` told me. These two cannot.

**Partial mitigation already in place, worth not breaking:** `CLAUDE.md` points at `AGENTS.md`
via GREP commands (`grep -nE '^- \*\*A[0-9]+ ' AGENTS.md`, and two others) rather than telling
anyone to read it end to end. Grep-first access is what makes a 368 KB reference usable at all —
this is exactly why the A-law summary in `CLAUDE.md` was replaced with a derivation command
earlier today instead of being extended inline.

**Not fixed here, and the fix is a real decision, not a tidy-up.** Options are (a) split
`AGENTS.md` by law family with an index, (b) age dated receipts out to `docs/audits/` and leave
pointers, or (c) add a size ratchet so growth becomes visible and deliberate. Each changes how
every agent in this repo finds things, so it wants a design pass rather than a late-session edit.
Recorded with the numbers so the next person starts from a measurement.

## AUDITED (2026-08-23): all 6 DEMAND_GATED rows verified — the board's staleness is NOT uniform

The 2026-08-23 closeout audit covered the 6 **BLOCKED** rows and found **2 stale** (F8 citing a
file that does not exist; MCP-SURFACE framed as rust-blocked when the contract version is a
Python one-liner). The 6 **DEMAND_GATED** rows had never been checked. All six hold:

| Row | Cited evidence | Verified |
|---|---|---|
| RUST-REPLACE-TOCTOU | residual-TOCTOU characterization pin in `backend_cpu.rs` | Present and **NOT inverted** — the row's own acceptance signal has not fired, so it is correctly still open |
| MCP-LEAN-DEFAULT | contract version 1.7.0 | Exact match |
| DD-006 | `_DAEMON_CONNECT_TIMEOUT_SECONDS` | Present |
| AST-DSL-PARITY | ast-grep wrapper backend | Present in `src/tensor_grep/backends/` |
| CONTINUOUS-REFRESH | warm session daemon | Present |
| #255 | `find_golden_corpus` | Present |

**The pattern is worth keeping:** the BLOCKED rows rotted because their evidence points at
line-level facts about code that moved. The DEMAND_GATED rows held because they cite
CONDITIONS ("demand for X", "this pin inverting") rather than locations. When writing a gated
row, cite the condition — a row that names a line number has a shelf life.

### Two of my own zeros in this audit were wrong, and both would have produced a false "stale"

- `grep request_queue_size src/tensor_grep/cli/session_daemon.py` → **0 hits**. That looks like
  DD-006 citing a symbol that does not exist. It is not: the daemon subclasses
  `socketserver.ThreadingMixIn, socketserver.TCPServer`, so `request_queue_size` is an INHERITED
  default and the row's wording ("default `request_queue_size=5` backlog") is exactly right. An
  explicit hit would have CONTRADICTED the row.
- AST-DSL-PARITY returned 0 because I globbed `src/tensor_grep/cli/` when the backends live in
  `src/tensor_grep/backends/`. Wrong path, not missing code.

A control (`ZzzNotARealSymbolXyz` → 0) proved the grep MECHANISM worked in both cases, which is
precisely what makes a wrong PATH the dangerous residual: the mechanism check passes while the
question goes unanswered. A control proves your tool runs; it does not prove you pointed it at
the right thing.

## MEASURED (2026-08-23): the `_add` trap splits in two, and only ONE half reproduces

The external dogfood's finding #1 — *"`prepare "add retry with tests"` → `repo_map._add` @
conf=1.0, ask=false"* — measured against the **published** v1.113.2 in a clean
`python:3.12-slim` container, on a corpus containing both `_add` and an obvious
`replace_with_retry`:

```
primary_symbol : '_add'      <- the trap symbol IS selected
primary_conf   : 1.0         <- at FULL confidence
overall        : 1.0
ask_required   : True        <- but the ask IS enforced
downgrade      : []
```

**The RANKING half reproduces.** `_add` wins for a query whose obvious intent is
`replace_with_retry`. A three-character underscore-prefixed helper beating a well-named
function on a query word is exactly the defect reported.

**The AUTO-EDIT half does not, on this corpus.** `ask_user_before_editing.required` is `True`,
so an agent following the contract would ask before editing. The report said `ask=false`; their
corpus differed from this one.

That split matters for severity. As "an agent silently edits the wrong symbol" this is a
safety bug. As "ranking picks a poor primary but still forces a human check" it is a quality
bug with a working backstop. **On the published build it is currently the second.** Nobody
should re-file it as the first without re-measuring, and nobody should close it as fixed
either — the ranking defect is real and present.

Note the `overall: 1.0` with `downgrade_reasons: []` is CORRECT and not the guard failing:
the degraded-confidence ceiling only applies when a reason exists, and here none does. The
ranking is confidently wrong, which no confidence guard can detect — a guard bounds the number
against the *reasons*, not against the *answer*.

**Method caveat worth keeping:** my first pass reported "trap NOT reproduced" because the
verdict expression required BOTH `_`-prefixed-at-high-confidence AND `ask_required == False`.
One compound assertion over two independent properties returns a single boolean that hides
which half failed. Assert the halves separately, or the measurement reports the opposite of
what it found.

## OPEN (2026-08-23): the shipped confidence guard fixes the CONTRACT, not the ASK-GATE

Filed against my own change (PR #1105, merged `65cf67f`, released v1.113.2) after an adversarial
security review of the shipped code returned CHANGES_REQUIRED with one HIGH.

### The measurement

`_DEGRADED_CONFIDENCE_CEILING = 0.99` (`agent_capsule_confidence.py:164`).
The ask-gate threshold is **0.75** (`agent_capsule_confidence.py:256`:
`if confidence_overall < 0.75: return "confidence below 0.75"`).

**0.99 is well above 0.75**, so the guard never changes `ask_user_before_editing.required`.

### What that means, stated plainly

The guard closes a real hole: a payload can no longer list the reasons it is degraded AND report
`overall == 1.0`. That contradiction is gone, and its tests are perturbation-proved.

It does **not** close the hole the external dogfood actually reported, which was
`downgrade_reasons` non-empty **and** `ask_required == false` — i.e. an agent editing without
asking on a scan that did not finish. With the guard, such a payload now reports 0.99 and the
agent **still** auto-edits.

PR #1105's body says *"`ask_user_before_editing` keys off that number, so the certain-but-degraded
shape tells an agent 'edit this, no question needed'"* — implying the guard prevents that. It does
not. That sentence overstated the fix and this row is the correction.

Note the severity is bounded by what was ALREADY correct: the branch-specific clamps
(0.94 / 0.72 / 0.55) do drop below 0.75 for truncation and primary-file omission, so those paths
already force the ask. The gap is the two branches that had no clamp of their own —
`consistency["confidence_downgraded"]` and a caller-supplied reason.

### Why it is not fixed in the same breath

Lowering the ceiling below 0.75 would make ANY downgrade reason force a human prompt, including
minor ones on otherwise-strong results. That is a change to an agent-safety threshold and a
change to how often the tool interrupts a user — a design decision, not a tuning nit. It wants a
council and a look at real capsule distributions, not a late-session edit.

### Also raised by the same review, unresolved

- **MED** — a non-finite `overall` (NaN) supplied via `edit_plan_seed.confidence.overall`
  propagates as NaN rather than a bounded value. `min(NaN, 0.99)` does not clamp. Never yields
  1.0, so it is not the certainty bug, but the output contract is not bounded either.
- **LOW** — a caller-supplied EMPTY STRING counts as a downgrade reason and lowers confidence to
  0.99. Cosmetic, but it means "a reason" is currently "any truthy-or-not list element".

## ROOT-CAUSED (2026-08-23): the "order-dependent help flake" is deterministic, not a flake

Filed after four sightings across three PRs. It is **not** flaky, **not** terminal-width
dependent, and **not** caused by any change in this session — it reproduces on pristine `main`.

### The measurement

`tg --help` renders **two different documents** depending on WHEN `tensor_grep.cli.main` is
first imported:

| how `main` is imported | help chars | command rows | contains `sidecar-routed GPU results` |
|---|---:|---:|---|
| at **module scope** (pytest collection time) | **10,429** | 50 | **NO** |
| inside the **test function** (run time) | **20,641** | 0 | **YES** |

Reproduce with nothing but a two-line test file:

```python
# module-scope import -> the SHORT render
from tensor_grep.cli.main import _scan_incomplete  # noqa: F401


def test_probe():
    from typer.testing import CliRunner
    from tensor_grep.cli.main import app

    print(len(CliRunner().invoke(app, ["--help"]).stdout))
```

Move that import inside the function and the number doubles. `COLUMNS=200` changes neither
number, so terminal width is ruled out.

### Why it looked like a flake

`tests/unit/test_cli_modes_ast_misc.py::test_app_help_should_expose_the_python_public_top_level_surface`
asserts a snippet that exists ONLY in the long render. It therefore fails whenever ANY
earlier-collected test module imports `tensor_grep.cli.main` at module scope — and passes when run
alone or as a whole file, because then nothing has imported `main` at collection time. The polluter
in the observed failures is `tests/unit/test_agent_capsule_best_effort_primary.py`, whose only
"offence" is a perfectly ordinary module-scope
`from tensor_grep.cli.main import _scan_incomplete`.

Bisected to that single file, then to import time specifically: the failure reproduces with **every
test in the polluter deselected**, so no test body is involved.

Counter-intuitive tell that misled the first two investigations: running MORE tests can make it
PASS, because a later-collected module can re-import and re-render.

### Why this is a product question, not just a test-hygiene one

Two renders of `--help` differing by 10 KB and by whether the agent-contract prose appears is a
user-visible surface. An agent that shells out to `tg --help` may get either document depending on
process state. The test is the messenger.

### Not fixed here, and why

The fix is either (a) make the help render deterministic regardless of import timing, or (b) make
the test import `main` the same way the assertion's render requires and pin BOTH renders. Both are
product/test-design changes outside a closeout's scope. Recorded with a reproduction so the next
session starts from the mechanism instead of re-bisecting it a fifth time.

### FIXED same day (option (a) taken) -- and the fix itself regressed once before landing clean

PR #1115 (merged `3524397`) moved the `TYPER_USE_RICH` Windows-EINVAL workaround from
`cli.main` **module-import time** into `cli.main.main_entry()`, making the pytest
order-dependent flake deterministic (CliRunner never reaches either `main_entry`, so the render
is now always the long/correct one regardless of import order). Verified: the exact
previously-failing polluter-order combination (`test_agent_capsule_best_effort_primary.py` +
`test_cli_modes_ast_misc.py`) now passes; full `tests/unit` 5835 passed (1 unrelated pre-existing
local env gap: `model2vec` extra not installed).

**That merge immediately broke the REAL Windows launcher**, caught only by post-merge CI on
`main` (not by the PR's own checks -- a PR-vs-push CI divergence): `windows-agent-readiness`
failed `public-search-advertised-flag-sweep` because `tg search --help` was missing
`--no-ignore-file-case-insensitive`. Root cause: via the real launcher chain
(`bootstrap.main_entry()` -> `_run_full_cli()` -> `cli.main.main_entry()`), something upstream
resolves Rich's render mode before `cli.main.main_entry()` runs, so setting the env var there was
too late -- Rich stayed enabled and truncated the flag name inside its box-drawn panel
(confirmed directly against the built `tg.exe`, not just tests: the raw output showed
`--no-ignore-file-case-insen...` cut mid-word).

**Fix, PR #1116** (branch `fix/windows-help-rich-truncation-entry-point`): moved the guard again,
this time into `bootstrap.main_entry()` -- the true single top-level entry for both the `tg`
console-script and `python -m tensor_grep` -- so it runs before anything touches Typer/Rich.
Verified against the real `tg.exe` launcher (flag renders uncut) and the full test suite (5835
passed, same 1 unrelated gap). Also bumped `scripts/file_size_allowlist.json`'s `bootstrap.py`
pin 1696 -> 1703 (the guard's comment is load-bearing; compressing it to dodge the ratchet would
be worse than a small, justified bump). CI on #1116 was still running the Windows-specific jobs
as of this note; merge is gated on `windows-agent-readiness` passing, not assumed.

**Lesson for the next session:** a one-shot-process env-var side effect that depends on renderer
internals (Rich resolving its mode lazily but not at the point you'd expect) cannot be validated
by CliRunner alone -- CliRunner never exercises the real console-script launcher chain at all.
Any fix to this class of bug needs a check against the built binary/launcher, not just the test
suite, before it's called done.

## Session closeout (2026-08-23) - state, receipts, and what was NOT done

Filed so a fresh session starts from measured state rather than from this session's prose.

### Open work, honest state + receipt

| Item | State | Receipt |
|---|---|---|
| **PR #1103** session/daemon root anchoring (G4.1, G4.2) | **SHIPPED and VERIFIED ON THE PUBLISHED ARTIFACT** | Merged `cb42752`; released **v1.113.1**, published **4/4 by filename** (`macosx_11_0_arm64`, `manylinux_2_39_x86_64`, `win_amd64`, sdist — checked per-artifact, never by version presence). Dogfooded on a clean `python:3.12-slim` with a stock `pip install tensor-grep==1.113.1`: the external auditor's exact repro (open a session in a SUBTREE, then `tg session show <id>` from the repo root with **no PATH argument**) now returns **OK**, and `search`/`defs` show no regression. This is the loop closed end-to-end: reproduced on the published v1.111.7, fixed, released, and re-verified on the published wheel — not on a branch and not on a maintainer's machine |
| **PR #1105** confidence invariant enforced in production | **SHIPPED -- released v1.113.2, PyPI 4/4 by filename** | 39 checks pass / 0 fail; RED reproduced on `origin/main` before the guard was written |
| **PR #1102** `blast-radius-render --deadline` | **SHIPPED 2026-08-23 -- released v1.113.4, PyPI 4/4 by filename** | The blocker was the file-size ratchet refusing +11 lines in `main.py`. RESOLVED by taking the option-factory route the row itself named: `_deadline_option(help_text)` replaced 20 duplicated blocks, taking `main.py` 13,523 -> 13,400 lines and freeing headroom under the pin. Merged `7e06a16`; `chore(release): v1.113.4` on main, verified COMPLETE 4/4 on PyPI with a positive control run before the target query |
| `_add` lexical trap at `confidence=1.0` | OPEN, unassigned | External dogfood #1; ranking quality, not a guard. Highest severity of the open set: an agent edits the wrong symbol at full confidence |
| Warm-path latency ~4s, `tg dogfood` timeout, absent rank scores | OPEN, unassigned | External dogfood #5/#6/#7 |
| `tests/unit/test_cli_modes_ast_misc.py` order-dependent help failures | OPEN | **Three sightings** this session (`test_app_help_...`, `test_search_help_...`, and #1102's `test_positive_control_both_siblings_have_help` in CI). Each passes alone, passes as a whole file, and passes on `main` — only fails inside a larger selection, so another test mutates the CLI help surface. Third sighting is this repo's trigger for a structural fix; the polluter was NOT identified (the two candidates checked were monkeypatch-restored) |

### Closeout steps that were N/A, not done

Three artifacts named in the closeout request **do not exist in this repository**, so the
corresponding steps were skipped rather than performed. Recorded because "skipped" and "done"
must not be confused by the next session:

- `.wayfinder/<slug>/MAP.md` — absent. There is **no answer key**, so `verify-feature` had
  nothing to run against. Not a failure; this repo has never used the wayfinder lane.
- `.orchestrator/state.json` — absent. Nothing to refresh. Creating one would be inventing a
  structure this repo does not use.
- `QUEUE.md` — absent, so `feature-batch` had no queue to keep accurate. There are also no
  `feature folders`; work here is tracked by this file plus `docs/TASK_BOARD.md`.

### Worktree harvest (2026-08-23)

`.claude/worktrees/` held **21 orphan directories, 5.2 GB**, none of which git could see
(`git worktree list` knew only the main checkout and one live temp worktree; `.git/worktrees/`
held exactly one admin entry). 12 carried content, 9 were empty. All were ≥15 days stale
(newest file 2026-08-08) with no process running from any of them.

Source-only slices (`src/`, `tests/`, `docs/`, `scripts/`, root `*.md`) were archived to
`~/.tensor-grep-worktree-archive/2026-08-23` (**408 MB**) before deletion, so any uncommitted
work survives at ~7% of the size. The rest was Rust `target/` build output.

Measurement note worth keeping: the first size probe reported **`non-build=0MB`**, which would
have justified deleting without archiving anything. A positive control over the repo's own
`src/` returned 58 MB and exposed the pattern as broken; the true figure was **1547 MB**. A zero
from an unproven probe is UNRESOLVED, not ABSENT.

## Recent campaign notes (2026-08-23) - EXTERNAL AGENT DOGFOOD of v1.113.0 + open findings filed

Second external agent dogfood, this time against **v1.113.0** (their binary
`~/.tensor-grep/bin/tg.exe`, corpus `C:\dev\agentwork\tensor-grep`). Filed here because this
session has no task-store; the board is the durable tracker.

| Their finding | Disposition, with the receipt | Owner |
|---|---|---|
| **#3** `session show/edit-plan` defaults `[PATH]` to `.`, so a session opened in a subtree is invisible from the repo root | **FIXED AND RELEASED in v1.113.1, dogfooded on the published wheel in a clean `python:3.12-slim`.** PR #1103 anchors `_resolve_root` to the project root. Their exact repro (open in subtree, `session show <id>` from root with NO PATH argument) re-run on that branch returns **exit 0**. Correct on v1.113.0 because the fix is not published yet | PR #1103 |
| **#4** `blast-radius-render` rejects `--deadline` its sibling accepts | **FIXED AND RELEASED 2026-08-23 in v1.113.4** (PR #1102, merged `7e06a16`). PyPI 4/4 by filename | PR #1102 |
| **#1** lexical primary trap: `"add retry with tests"` selects `_add` at `confidence=1.0`, `ask=false` | **OPEN, no owner.** The highest-severity row here: an agent edits the wrong symbol at full confidence. Needs a weak-lexical/stop-symbol gate (a 1-3 char or `_`-prefixed symbol matched from a query word must not be primary at conf >= 0.9) plus a dogfood known-bad case | unassigned |
| **#2** `downgrade_reasons` non-empty while `confidence.overall=1.0` | **OPEN.** Invariant tests exist on main (`d29b013`, `tests/unit/test_prepare_confidence_invariants.py`) but the external run still reports the violation, so the invariant is **not actually enforced on the production path** -- the tests pass without binding it | unassigned |
| **#5** warm session ~4.0-4.3s vs cold ~6.8s; `response_cache_hits=0` | **OPEN.** Note this is the same *class* as G4.2 (warm cache unreachable) but measured with a correct cwd, so PR #1103 is not automatically its fix -- re-measure after #1103 lands before scoping. **UNPARKED 2026-08-23: #1103 HAS landed (v1.113.1, dogfooded on the published wheel), so the stated precondition is met and this is now measurable rather than blocked.** The re-measure must run against the PUBLISHED wheel, not a branch, and must record whether `response_cache_hits` is still 0 -- a warm/cold delta alone cannot distinguish a cache that misses from a cache that is never consulted | unassigned, AI-doable |
| **#6** `tg dogfood` still red on `agent-readiness-timeout` (170s) | **OPEN.** Public-version checks now pass when pinned with `--expected-version`, so this is the only remaining failure | unassigned |
| **#7** `--rank --json` exposes no scores; `tg find` still BM25-only | **OPEN**, low | unassigned |

### Other findings from the same session (not from the external audit)

- **The canonical index in `docs/TASK_BOARD.md` is stale.** It is stamped `v1.110.16 / main 8f7db83`
  while main is at `v1.113.0`+; it asserts **0 IN_FLIGHT** while PRs #1102 and #1103 are open; and
  its own count says **5 CEO_GATED** while the 2026-08-22 campaign note names **six**
  (`#48 #72 #77 #131 #169` **+ RULESETS**). Re-derive the index from live state.
- **F8's blocker receipt cites a file that does not exist.** It names
  `rust_core/src/path_domain.rs`; `git cat-file -e origin/main:rust_core/src/path_domain.rs` fails.
  The block itself (shared-box cargo/e2e ban) may still hold, but the stated evidence does not.
  Re-derived and confirmed live in the same pass: **MCP-SURFACE** is accurate
  (`_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` at `cli/mcp_server.py:188`) and **F6** is accurate
  (`evidence_signing.py` = 539 lines / 19 functions).
- **`tests/unit/test_cli_modes_ast_misc.py` has an order-dependent failure.** Two sightings this
  session (`test_app_help_should_expose_the_python_public_top_level_surface` and
  `test_search_help_should_render_python_search_help_smoke`). Each passes alone, passes as a whole
  file, and passes on main -- they fail only inside a larger selection, so another test mutates the
  CLI help surface. Third sighting triggers the structural fix.
- **RULESETS is NOT a live defect, contrary to an earlier claim in this session.** Measured on a
  clean `python:3.12-slim` with a stock `pip install tensor-grep==1.113.0`: `tg rulesets` prints
  `WARNING: the ast-grep backend is not installed ... pip install ast-grep-cli`, and
  `tg scan --ruleset subprocess-safe` fails **closed** with that same remediation plus the explicit
  sentence that a stock install does not include it. After `pip install ast-grep-cli` the scan runs
  and correctly reports `matched_rules=1` on a `shell=True` fixture. The earlier "still broken"
  reading came from passing `--ruleset security`, which is a **category, not a ruleset** -- the CLI
  says so and lists the six valid names. The remaining RULESETS question is the CEO-gated packaging
  one (a `tensor-grep[scan]` extra), not a correctness bug.

## Recent campaign notes (2026-08-23) - G4.2 REPRODUCED: the warm cache is unreachable from a SUBTREE path

Fourth of the external dogfood's five UNVERIFIED findings. The report said warm
`session edit-plan` was ~2.9s and the daemon showed `response_cache_hits=0`. **Reproduced, and
the mechanism is a real product defect, not a cold cache.**

### The measurement (published v1.111.7, daemon RUNNING)

Two identical `tg defs src missing_scan_paths --json` calls against a live daemon:

```
call 1 (cold)  2505 ms
call 2 (same)  2702 ms      <- SLOWER, no warm benefit at all
counters:      response_cache_hits=0  entries=0  cache_misses=0
```

`cache_misses = 0` is the tell, and it is the reason this is a defect rather than a cold start.
A miss would prove the daemon was consulted and had nothing. **Zero misses proves it was never
consulted.**

### The control that isolates it

Same daemon, same symbol, only the PATH argument changed:

| query path | daemon counters after |
|---|---|
| `src/` (a subtree of the daemon root) | `misses=0, entries=0` — **daemon never consulted** |
| `.` (exactly the daemon root) | `misses=1, entries=1` — consulted, stored |
| `.` again | **`hits=1`** — cache works correctly |

So the response cache is functioning perfectly. It is simply **unreachable unless the query path
exactly equals the daemon root**.

### Why this matters more than the reported symptom

The tool's own guidance tells agents to scope queries to a subdirectory — `tensor-grep-prepare`
says *"Prefer `REPO/src`"*, and `tensor-grep-find-and-route` says *"always scope `tg find` to a
PATH"*. Following that advice **silently disables the warm-daemon moat**. An agent doing exactly
what the docs recommend gets cold-path latency and a daemon reporting `hits=0`, with no signal
explaining why.

There is no honesty field for this either: the payload does not say "daemon skipped: path is not
the daemon root". It just runs cold.

### Status

VERIFIED, not fixed. The fix touches daemon path-matching (root vs subtree containment) and is
shared by every warm-path command, so it needs its own RED-first change with a subtree fixture and
a cross-check that a subtree query cannot read a STALE root-scoped entry. Filed rather than patched
into an unrelated branch.

**Kin to G4.1**: both are path-identity bugs in the session/daemon layer — G4.1 is cwd-keyed
STORE selection, this is root-equality daemon selection. A fix for either should check whether it
also resolves the other.

## Recent campaign notes (2026-08-23) - G4.4 REPRODUCED, G4.5 NOT REPRODUCED (opposite numbers)

Two more of the external dogfood's five UNVERIFIED findings, probed against the published
v1.111.7 wheel.

### G4.4 — `tg dogfood` version-skew FAIL: **REPRODUCED**, with a sharper diagnosis

```
pyproject.toml : 1.112.0      <- the worktree (a release landed)
installed tg   : 1.111.7      <- what every probe actually executes
verdict        : FAIL, agent-readiness passed=15 failed=8
```

All 8 failures are ONE cause, and the payload says so verbatim:

```
agent_readiness.expected_version = 1.112.0
public-version-powershell :: expected one of ['tensor-grep 1.112.0', 'tg 1.112.0']
                             in version output, got ['tensor-grep 1.111.7']
```

`expected_version` is read from the **worktree's `pyproject.toml`**, while every `public-version-*`
and `public-doctor-*` probe runs the **installed binary**. The gate is comparing a CHECKOUT against
a PUBLISHED ARTIFACT and calling the difference a failure. The reporter's phrasing — *"confuses
'published binary' vs 'this checkout'"* — is exactly right.

Their proposed fix is sound: default `--expected-version` to the INSTALLED binary, with an optional
pin to pyproject for the release-verification case. Two different questions ("does the published
artifact work" vs "does this checkout match what shipped") currently share one default, and the
default answers the rarer one.

**Probe honesty:** my first attempt at this read a top-level `checks` key and reported `failed
checks: 0` while the text output said 8. That was MY probe being wrong, not a payload/text
divergence in the product — the failures live under `verdict.failed_checks` and
`agent_readiness.results`. Recorded because "JSON says 0, text says 8" would have been a plausible
and completely false product bug.

### G4.5 — LSP provider split-brain: **NOT REPRODUCED**, and the numbers are the OPPOSITE

Reported: `defs --provider lsp` -> `fallback-native, lsp_count=0`, while `agent --provider lsp`
claimed `lsp_proof=true` on a native anchor — i.e. the weaker command over-claiming.

Measured here:

| Command | Reported | Measured |
|---|---|---|
| `defs --provider lsp` | `fallback-native`, `lsp_count=0` | `lsp_evidence_status=lsp_proof`, `lsp_count=1`, `native_count=1`, `merged_count=1`, **`fallback_used=False`**, full `provider_agreement` object present |
| `agent --provider lsp` | `lsp_proof=true` on a native anchor | `lsp_proof=None`, no `provider_agreement` key at all |

So on this machine `defs` is the command with the RICHER evidence and `agent` is the one carrying
NO lsp fields — the reverse of the report. The most likely explanation is environmental: their
Pyright was not serving that symbol (hence `lsp_count=0`), mine was.

**This is NOT a refutation.** It means the finding is environment-dependent, which makes it a
worse bug to reason about, not a lesser one: the same command emits different honesty fields
depending on whether a language server happens to be warm. What both runs agree on is the
reporter's actual concern — **an agent cannot tell from the payload alone whether "lsp" evidence
is real**, because `agent` ships no `provider_agreement` object to cross-check while `defs` does.
That asymmetry reproduces here and is the part worth fixing.

### Running tally on the five unverified findings

| # | Finding | Verdict |
|---|---|---|
| G4.1 | session cwd footgun | **REPRODUCED** — worse than reported (two cwd-keyed stores; `list` returns 64 wrong sessions) |
| G4.2 | warm-path latency / `response_cache_hits=0` | not yet probed |
| G4.3 | Windows AST `run` argv fragility | not yet probed |
| G4.4 | `tg dogfood` version skew | **REPRODUCED** — checkout-vs-published comparison |
| G4.5 | LSP provider split-brain | **NOT REPRODUCED** — opposite numbers; environment-dependent, and the payload asymmetry is the real finding |

## Recent campaign notes (2026-08-23) - G4.1 VERIFIED: session store is cwd-keyed, and `list` answers from the WRONG store

The external agent dogfood (v1.111.7) reported: `tg session open src` then `tg session show <id>`
from the repo root returns "Session not found", while `list` works from the parent. **Reproduced
exactly, and the mechanism is worse than the report describes.**

### Reproduction (published wheel, v1.111.7)

```
$ tg session open src --json
  session_id: session-20260823004352130555-src-6dc6b0f9

$ cd src && tg session show session-20260823004352130555-src-6dc6b0f9 --json
  {"version": 1, ...}                      # WORKS

$ cd .. && tg session show session-20260823004352130555-src-6dc6b0f9 --json
  Session not found: session-20260823004352130555-src-6dc6b0f9   # SAME ID, one dir up
```

### The mechanism — TWO stores, selected by cwd

```
src/.tensor-grep/sessions/     <- holds the new session (1 match)
.tensor-grep/sessions/         <- 67 files, holds 0 matches for that id
```

`tg session list` from the repo root returns **64 sessions and does not contain the one just
opened**. That is the part the report understates: this is not a "not found" error, it is a
**confidently wrong answer**. An agent that opens a session, then lists from a parent directory,
receives a plausible non-empty list that silently omits its own session. A missing-item error is
recoverable; a wrong list that looks complete is not.

### Why it is fixable

The session id **already carries its root token**: `session-<ts>-src-<hash>`. So `show` has enough
information in the id itself to resolve which store to read, without depending on cwd. The reporter's
proposed fix ("resolve session store from ID, or require `--root` and error with the exact path to
use") is therefore implementable, not aspirational.

### Severity for agents

HIGH. An agent's cwd changes between turns for ordinary reasons (a tool call, a subprocess, a
worktree). A session handle that silently changes meaning with cwd is a state bug an agent cannot
detect from the payload -- both answers look successful.

### Status

VERIFIED, not fixed. The fix touches session-store resolution, which is shared state used by
`open`/`show`/`list`/`edit-plan`; it needs its own RED-first change with a two-store fixture, and
must not silently re-point existing sessions. Filed rather than patched inside an unrelated branch.

## Recent campaign notes (2026-08-22) - EXTERNAL AGENT DOGFOOD of v1.111.7: triage with per-finding verification

An external AI agent ran a full agentic dogfood of `tg 1.111.7` against a DIFFERENT checkout
(`C:\dev\agentwork\tensor-grep`) using the real installed binary, and filed ~10 defects plus 8
feature requests. **Every finding below carries what I did to check it**, because a dogfood report
is a hypothesis like any other agent output — and two of its two CRITICAL findings did not
reproduce here, which is information, not a dismissal.

### Verification results

| # | Finding | Reporter severity | My verification | Status |
|---|---|---|---|---|
| 1 | **Lexical target trap** — `prepare src "add retry with tests"` returned `repo_map._add` at confidence **1.0** with `ask_user_before_editing=false` | Critical | Re-ran on THIS tree at v1.111.7: primary was `_raw_validation_plan_for_tests`, confidence **0.55**, `ask_required=True` | **NOT REPRODUCED here** — corpus-dependent, NOT refuted |
| 2 | **Confidence vs downgrade desync** — `partial=true` + truncation reasons while `confidence.overall=1.0` | High | Forced truncation (`--deadline 3`): `partial=True`, 3 downgrade reasons, `overall=0.94`, `ask_required=True`. Both invariants held | **NOT REPRODUCED here** — now PINNED, see below |
| 3 | **Flag surface inconsistency** — `blast-radius --deadline` works, `blast-radius-render --deadline` does not | Medium | `tg blast-radius --help` contains `--deadline`; `tg blast-radius-render --help` does **not** | **CONFIRMED** |
| 4 | **rg parity requires `--sort path`** — unsorted order diverges | Low | md5 of first 3 lines differs between `tg --format rg` and `rg` unsorted | **CONFIRMED** (rg itself does not guarantee unsorted order; this is a DOC gap, not a bug) |
| 5 | Session cwd footgun — `session show` fails from a parent dir while `list` works | High | **NOT YET CHECKED** | UNVERIFIED |
| 6 | Warm path not sub-second; `response_cache_hits=0` | Medium | **NOT YET CHECKED** | UNVERIFIED |
| 7 | AST `run` fragile under Windows shells (`$` expansion; 0 matches on `def $NAME($$$)`) | Medium | **NOT YET CHECKED** — plausible; matches the known Windows argv-quoting class | UNVERIFIED |
| 8 | `tg dogfood` FAILs on a worktree whose pyproject version != installed binary | Medium | **NOT YET CHECKED** | UNVERIFIED |
| 9 | LSP provider split-brain — `defs --provider lsp` → `fallback-native, lsp_count=0`, while `agent --provider lsp` → `lsp_proof=true` with `lsp_resolution_basis=native-definition-anchor` | Medium | **NOT YET CHECKED** | UNVERIFIED |
| 10 | `tg find` dense-absent easy to misread as full hybrid | Low-Med | Independently observed the same day: exit 0, `find_bm25_only`, stderr names `tg install-dense` | **CONFIRMED as behaviour**; disagree it is a defect — the payload carries `routing_reason` + `rank_fallback_reason` |

**Do not treat rows 5-9 as closed.** They are unverified, not refuted. Each needs its own probe
before it is either fixed or dismissed.

### What shipped from this report

**The confidence invariant is now a permanent ratchet** — `tests/unit/test_prepare_confidence_invariants.py`:

```
I1  downgrade_reasons non-empty  =>  confidence.overall < 1.0
I2  partial is true              =>  confidence.overall < 1.0
```

This is the reporter's NFR-2, and it shipped **even though the bug did not reproduce**, because:
a contract that happens to hold today is exactly the thing to pin before it drifts, and the failure
shape they describe (certainty about a truncated scan) is the single most dangerous payload this
tool can hand an agent. The test carries TWO negative controls (the detector must flag each
violating shape) and a POSITIVE control (a correctly-downgraded payload must produce no
violations) — without the positive control, a detector that flagged everything would pass both
negatives and be useless.

The invariant is a PURE FUNCTION over the payload so its failure path is testable without the
CLI. A check that can only be exercised by a slow subprocess tends never to have its failure path
tested at all.

### What did NOT ship, and why

- **The ranking fix (their NFR-1/NFR-5, stop-symbol filter).** A short lexical token winning as
  primary is a RANKING concern, not a contract concern, and an invariant cannot express it. It
  needs a corpus-based golden set with known-bad queries — the same discipline
  `tensor-grep-semantic-search-campaign` already uses for retrieval quality. Filed, not guessed at.
  **Their proposed discriminator is good and should be reused verbatim:** a known-bad query must
  either set `ask.required=true` or refuse to name a primary.
- **Anything for rows 5-9.** Unverified findings do not get fixes; that is how a wrong fix ships.

### The reusable lesson

The report's most valuable property is that it ran the REAL binary against a DIFFERENT checkout.
Two of its critical findings did not reproduce on ours — which is itself the finding: **capsule
confidence and target selection are corpus-dependent, so a single-corpus verification cannot
falsify them.** That argues for the golden-set approach over one-off reproduction attempts, and it
is the same lesson as [[tensor-grep-dogfood-real-corpus-before-shipping-precision-2026-07-03]]:
fixture-green is false for heuristics.

## Recent campaign notes (2026-08-22) - WAVE 0a: the 6 BLOCKED rows RE-DERIVED against the tree

The 2026-08-22 unblock plan was council-audited and the audit's falsifiable claims were then
checked against the code. **Three of the six BLOCKED rows are STALE** — they cite files or work
states that the tree contradicts. Sequencing a campaign from them would have sent a builder at a
file that does not exist.

This is the [[verify-plan-against-code]] Step-0 failure in its purest form: every citation in the
rows resolves against a DOCUMENT, and none of them had been resolved against the TREE.

### Method

For each of the 6 canonical BLOCKED rows (`#89`, `#90`, `F5`, `F6`, `F8`, `MCP-SURFACE`), every
file path the row names was resolved with `ls`/`grep` against `main` at the time of writing.
A row is STALE if any path it names is absent, or if the work it calls "remaining" is present and
wired.

### Findings

| Row | Row says | Tree says | Verdict |
|---|---|---|---|
| **F8** | blocked on `rust_core/src/main.rs` + **`path_domain.rs`** + e2e routing parity | `rust_core/src/path_domain.rs` **DOES NOT EXIST**, and `grep -rln "path_domain\|PathDomain" rust_core/src/` returns **nothing** — the concept is absent from the Rust tree entirely | **STALE** — re-scope before any work |
| **MCP-SURFACE** | blocked on Task 2C, which "modifies `rust_core/src/main.rs`" | `_TG_MCP_SERVER_CONTRACT_VERSION` lives at **`src/tensor_grep/cli/mcp_server.py:188`** (Python). `main.rs` contains **0** contract references | **STALE dependency** — the contract bump is a Python change and is not cargo-blocked at all |
| **F6** | "Python/schema/evidence-signing slices are buildable-first ... remainder BLOCKED" | `src/tensor_grep/cli/evidence_signing.py` is **539 lines / 19 functions**, imported by `audit_manifest.py`, `checkpoint_store.py` and `evidence_receipt.py` | **PARTLY SHIPPED** — the evidence-signing slice is built and wired, not remaining |
| **F6** (native half) | native `verify-edit` still blocked | `grep -rn "verify-edit\|verify_edit" src/tensor_grep/cli/main.py` returns **nothing** — the surface genuinely does not exist | **ROW CORRECT** on this half |
| **F5** | blocked on `rust_core/**` + `tests/e2e/**` | both paths exist; the glob is unresolved, so "independent of the MCP chain" is **not assertable** from the row | **UNDER-SPECIFIED** — needs exact touch-points before sequencing |
| **#89 / #90** | WSL path-domain reproduction; needs a real WSL host | no file citations to falsify; the WSL constraint is real and is NOT removed by the ubuntu container | **ROW CORRECT** |

### What this changes

1. **F8 cannot be planned until it is re-scoped.** Its central file does not exist. Whether the
   work moved, was absorbed, or was never started is an open question — do NOT assume it moved to
   `runtime_paths.rs` without checking; that is the nearest-name guess, not evidence.
2. **MCP-SURFACE is probably NOT cargo-blocked.** If the only thing Task 4 needs is the contract
   version, that is a Python edit plus its validator test and the 5-registration-site rule. The
   Task 2C dependency was asserted from a `main.rs` premise that measurement does not support.
3. **F6's disposition is MIXED and its shipped half must be closed**, not carried as remaining
   work. Carrying shipped work as open is how 17 stale items accrued once before.
4. **The container does NOT unblock #89/#90.** Those need a real WSL host. The harness removes the
   *cargo* constraint, never the *WSL* one — the plan says this and the rows agree.

### Discipline note

A prior campaign entry (TASK_BOARD, W6) records "six BLOCKED rows re-derived". That re-derivation
did NOT catch `path_domain.rs`, which means either the file was removed afterwards or the
re-derivation checked row TEXT rather than file EXISTENCE. **A re-derivation that does not resolve
every cited path against the tree is a proofread, not a re-derivation** — and it produces exactly
the confidence that stops the next person from checking.

## Recent campaign notes (2026-08-22) - CEO-GATE COUNCIL: 5-seat verdicts on #48/#72/#77/#131/#169 + RULESETS

Council run 2026-08-22 (`tt_council.sh`, 7 seats dispatched). **5 substantive seats**: `claude`
(fable-5), `droid_kimi`, `droid_deepseek`, `droid_glm`, `cursor`. **2 non-votes**: `agy` and `codex`
both hit sandbox/hook read failures and reported `CANNOT_READ_REQUIRED_FILE` **without fabricating
verdicts** -- the correct behaviour, and the reason a read-failure seat is discarded rather than
counted. Quorum (4) met. Question file: `.claude/thinktank_ceo_gates.md`.

**These are RECOMMENDATIONS. Every row stays `CEO_GATED` until the operator decides.**

| item | vote | council position |
|---|---|---|
| **#48** native front door | 4 agree / 1 nuanced dissent | Accept the shipped hybrid; retire the larger rewrite unless pip/uv parity is explicitly prioritised. |
| **#72** public benchmark | **5/5 WITHDRAW** | Withdraw the public 7.5x. Do NOT swap to 6.4x. No headline multiple until a committed, noise-calibrated harness exists. |
| **#77 / F9** ledger scope | 5/5 agree | Local opt-in advisory only; no auth/CI blocking. |
| **#131** GPU asset | 5/5 agree | Ship the optional experimental NVIDIA asset, CPU default + fallback, **no speed claim**. |
| **#169** GPU spend | 5/5 later, not now | Do not fund now. Rent when triggered (~$10-30 bounded campaign, $0.50-2.00/hr class), never purchase. |
| **RULESETS** | 4/5 pick (b) | Add a `tensor-grep[scan]` extra + `install-scan` remediation; keep advertise-then-disclose on the base install. Not a hard dependency. |

### #72 is the urgent one, and it is unanimous

Not one seat defended keeping the 7.5x public. The reasoning is consistent across providers: two
conflicting measurements (7.5x, later 6.4x) with **no committed harness** means neither number is
defensible, so revising the figure inherits the same defect as keeping it. Honest interim wording
exists -- task-class prose ("materially fewer tokens on definition-lookup in internal runs") -- but
it must not be a single multiple. Publishing again requires the four-part noise spec: measurement
surface, no-op control establishing the noise floor, SNR threshold, kill condition.

### Minority views, preserved (a 2-vs-3 minority is right ~25% of the time)

`droid_deepseek` dissented twice and both are worth keeping:

1. **#48** -- accept the hybrid but pin the cold-start floor in a **public ADR** rather than
   "retiring" the rewrite, so the decision stays revisitable against a measured number.
2. **RULESETS** -- the current advertise-then-disclose is the **least** acceptable option, because
   the shipped `rulesets_unavailable_reason` disclosure was an honesty **PATCH**, not a product
   **DECISION**. Its ordering: stop advertising what cannot run, THEN add the extra. This is a
   sharper reading of the same evidence the (b) majority used and should not be discarded as noise.

### #169 changed shape once a seat's claim was VERIFIED

`droid_kimi` argued #169 needs **$0** because the operator already has local NVIDIA hardware. That
is a factual claim about the environment, not about the repo, so it was checked rather than taken:

```
$ nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
NVIDIA GeForce RTX 4070, 12282 MiB
NVIDIA GeForce RTX 5070, 12227 MiB
```

Confirmed. This materially narrows #169: GPU **correctness** work needs no spend at all. The
residual question is only whether a **public** GPU claim needs a clean-room runner that a shared
desktop cannot be -- which is a much smaller decision than "fund a GPU proof environment", and it
is downstream of #72's harness discipline anyway.

### EXTERNAL GROUNDING (Exa, 2026-08-22) — added because a 5-seat consensus is not evidence

The council was run WITHOUT external research first. That is the documented correlated-hallucination
risk (consensus is not verification), so the two load-bearing items were then grounded against
outside sources. **Both changed materially.** Sources are cited so a reader can check them.

#### #72 — the external standard is stricter than the council's, and supplies the missing specifics

The council said "withdraw and re-measure with a committed harness" but gave no protocol. The
literature does:

| source | what it adds that the council did not have |
|---|---|
| Codeflash benchmarking docs (`docs.codeflash.ai/codeflash-concepts/benchmarking`) | A concrete noise floor: **5% on a real machine, 10% on GitHub Actions**, with significance only above it. Also argues for the **MINIMUM** across runs, not the mean — noise is strictly additive, so the min is the least-contaminated estimate of intrinsic speed. Directly relevant: our CI is GitHub Actions, so a sub-10% effect measured there is not a result. |
| SIGPLAN Empirical Evaluation Guidelines via `pldi-reproducibility` | **Dozens of repetitions, not three**; **geomean for ratios**; state warmup vs steady-state vs cold-start as different claims; pin the whole toolchain ("GCC" is not a baseline); and **benchmark survivorship** — silently excluding the cases your tool loses on is "the most damaging silent choice". |
| `fak/BENCHMARK-GOVERNANCE.md` | **Never mix regimes** — a live wall-clock speedup and a "value-add" ratio have different baselines and are not comparable. Anti-inflation rules: one primary number, baseline always stated, no cherry-picking, reproduction command required, and **tombstone superseded claims (mark them, do not silently delete)**. Plus a THEORETICAL / MEASURED / VERIFIED status ladder that must appear beside every published number. |
| Multigrid, "Reporting a Benchmark Result Honestly" | Eight required context fields; an explicit `runs_discarded` honesty field; and **round to the precision you earned** — "if your presentation needs two decimal places to show a difference, there is no difference." |
| Doppler `benchmark-methodology.md` | **Interleaved paired sampling** (alternate arms adjacently, never all-A-then-all-B), **>= 20 valid pairs**, paired 95% CI on the difference, and an explicit stopping rule: an interval crossing zero with < 0.5% median difference is **parity — stop tuning**, and a point estimate must not be published as a win in that case. |
| NIST TN 1830 (Pieterse & Flater) | Confidence intervals are what separate a real difference from random fluctuation; comparing bare averages "often leads to incorrect conclusions". |

**What this changes about our situation.** The 7.5x-vs-6.4x discrepancy has an explanation nobody in
the council proposed: **they may be different REGIMES measured against different baselines**, which
under fak's rule are not comparable at all and neither supersedes the other. Before re-measuring,
the first question is not "which number is right" but "what baseline and regime did each one use" —
and if that cannot be recovered from the artifacts, both are unpublishable regardless of a re-run.

**Concrete protocol this yields for the re-measurement (supersedes the vaguer council wording):**
interleaved paired arms; >= 20 pairs; report median + paired 95% CI (and min, per Codeflash);
geomean if aggregating across a suite; >= 10% floor if measured on GitHub Actions; publish the
regime and baseline in the same sentence as the number; commit the raw pairs and a reproduction
command; tombstone the 7.5x rather than deleting it.

#### RULESETS — the shipped remediation IS the documented best practice, and there is a 3rd option

- `pypa/packaging.python.org` issue **#1605** ("Should include guidance on how to handle missing
  optional dependencies / extras at runtime") confirms the gap and the accepted workaround: catch
  the ImportError and **raise a helpful error naming the extra**, because the default experience is
  a bare `ModuleNotFoundError` with no hint. That is exactly what the 2026-08-21 remediation fix
  shipped — so that fix is aligned with the ecosystem norm, not a local invention.
- **PEP 771 (Default Extras)** is a third option the council never raised. It exists *precisely* for
  this failure mode: "In all three cases, installing the package without any extras results in a
  **broken installation**, and this is a commonly reported support issue." It would let
  `pip install tensor-grep` pull the scan backend by default while `tensor-grep[]` stays minimal.
  **Status check before anyone builds on it:** PEP 771 is a PROPOSAL — it must be confirmed
  accepted and supported by the pip version we support before it can be a shipping plan. Treat it
  as a watch item, not an available mechanism.
- Net: the (b) majority (add a `[scan]` extra + remediation) is consistent with current standards.
  `droid_deepseek`'s minority point stands and is reinforced — PEP 771's motivation is that an
  install which advertises a feature it cannot run is a *known* packaging anti-pattern, not merely
  an honesty nit.

### Council hygiene notes for the next run

- `agy` failed on a Google Cloud telemetry hook (`Cannot find module ...telemetry_hook_bundle.js`)
  blocking its file tools; `codex` was refused shell access by its read-only sandbox. Both seats are
  recoverable and neither failure is about this question.
- A seat that reports a read failure AND emits a `RECOMMENDED:` line is not automatically valid:
  `codex`'s verdict line honestly asked to be re-run with read access rather than issuing positions,
  so it is a non-vote on substance even though it carries the token.

## Recent campaign notes (2026-08-22) - RUST-INDEX-LOCK-WALLCLOCK-FLAKE (P2, blocks releases, fix NOT yet applied)

- **Finding (2026-08-22): `index_lock::tests::heartbeat_keeps_a_slow_holder_alive_past_the_stale_threshold`
  fails on `test-rust-core (windows-latest, stable)` on a loaded runner, and one such failure
  SKIPS THE ENTIRE RELEASE CHAIN.** Measured on main run `32557799696` (head `e2621dca`):
  `test result: FAILED. 188 passed; 1 failed`, panic at `rust_core/src/index_lock.rs:548`, message
  `must not hang`. `Semantic Release`, both `build-pypi-*` jobs, `publish-pypi` and
  `validate-pypi-artifacts` all reported `skipped`.
- **The failing assertion is a wall-clock budget, and the CORRECTNESS assertion beside it PASSED:**

  ```rust
  let result = IndexLockGuard::acquire_with(&index_path, Duration::from_millis(500), stale_after, poll);
  assert!(result.is_err(), "a live, heartbeating holder's lock must never be stolen");  // PASSED
  assert!(started.elapsed() < Duration::from_secs(2), "must not hang");                 // FAILED
  ```

  So the property the test exists to prove -- a live heartbeating holder is not stolen from -- held.
  What failed is a 2s wall-clock bound over a 500ms acquire timeout (4x margin) on a contended
  Windows runner. This is the repo's own documented class: *a test holding a hard wall-clock budget
  is a contention detector, not a correctness test* -- it snaps first when a shared runner loads,
  while everything else merely slows and still passes.
- **Not caused by the change under test.** PR #1086 (the head that failed) touched Python, docs and
  tests only; it never went near `rust_core/`. A re-run of the same lane was dispatched.
- **Why it is not merely cosmetic:** per the repo law *a correctly-diagnosed flake still holds its
  AUTHORITY*. This one gates `Semantic Release`, so a loaded runner silently withholds a PUBLISH.
  It also presents as *"1 red Rust test on your commit"*, not as *"the runner was busy"*, which
  trains people to wave it through on the day it catches something real.
- **Proposed fix (NOT applied -- needs a Rust reviewer and cannot be verified on this box, which is
  a shared server where `cargo test` is forbidden):** separate the two properties. `acquire_with`
  already takes an explicit 500ms timeout, so *bounded return* is proven by the call returning at
  all plus `result.is_err()`; the elapsed assertion adds contention sensitivity for little
  additional coverage. Either drop it, or move the wall-clock arm to a non-gating lane and keep the
  deterministic arm (`is_err()` + a stubbed clock) in the gating one. **Do NOT "fix" it by widening
  the budget** -- that was tried on a sibling lane on 2026-07-27, bought 4x the wasted wall-clock,
  and was reverted the same day.
- **Measured frequency (2026-08-22), so this is not left as an open question:** across the last 12
  main `ci.yml` runs, the lane materialised on 5 and reported **4 success / 1 failure** -- real but
  intermittent, roughly 1 in 5 of the runs that reach it. Two caveats on that number: `<absent>`
  (7 of 12) means the job never existed on that run because it was cancelled early or `needs:`-gated,
  which is NOT a pass and must not be counted as one; and the failing run's own record now reads
  `<absent>` because the re-run REPLACED its job entry -- the remedy mutated the evidence, so re-run
  before you census, or the sample loses exactly the row you care about.
- **Recommendation given that rate:** the re-run is an adequate remedy for a single occurrence, so do
  not churn Rust today. Re-measure after ~10 more main runs; if the rate holds at ~20% it gates
  releases often enough to be worth the deterministic split above.
- **Superseded open question:** how often does this lane fail? If it is a one-off, the
  re-run is the whole remedy and the test change is not worth the churn. Count occurrences across
  recent main runs before editing Rust.

## Recent campaign notes (2026-08-21) - LEDGER-FIXTURE-SKIPS-ITS-OWN-TEST (P1, FIXED, no CEO gate)

- **Finding (2026-08-21): the CI `code` path filter did not watch `docs/audits`, so a PR editing
  the handler-disposition ledger skipped every `test-python` lane - including the test that reads
  that ledger.** `.github/workflows/ci.yml`'s `changes` job builds `CODE_FILES` from
  `src rust_core tests scripts benchmarks .github/workflows pyproject.toml Cargo.toml Cargo.lock
  uv.lock`. `docs/audits/2026-08-20-handler-dispositions.json` is **test input**, not
  documentation: `tests/unit/test_handler_dispositions.py::test_ledger_locatability` reads it and
  asserts every record still resolves to a real handler. Because it lives under `docs/`, a
  ledger-only PR was classified docs-only.
- **Measured on PR #1089**, whose entire purpose was to fix a `test_ledger_locatability` failure:
  the rollup showed **3 `test-*` checks, 19 SKIPPED, zero failing, PR green**. A control separates
  this from a runner problem: sibling PR #1086, same day, same repo, showed **12 `test-*` checks**.
  The count was the only visible difference - a skipped lane and a passing lane are
  indistinguishable in the rollup's pass/fail summary.
- **This is the `scripts/` hole already documented in `ci.yml`, with the roles reversed.** That
  comment states it exactly: *"The suite ran when the TESTS changed but not when their SUBJECT
  did."* Here the suite did not run when its FIXTURE changed. The same filter has now been wrong in
  both directions, which is the argument for enumerating what each lane CONSUMES rather than
  patching paths one incident at a time.
- **Blast radius was bounded, and the bound is the dangerous part.** `test-python`'s condition is
  `github.event_name != 'pull_request' || needs.changes.outputs.code == 'true'`, so pushes to
  `main` always ran it. The gap was **pre-merge verification only** - the fix could not be proven
  before landing, and the failure would surface on `main` after merge, where it is expensive.
- **Fix (shipped in #1089):** added `docs/audits` to `CODE_FILES`, pinned in
  `tests/unit/test_ci_code_path_filter.py::REQUIRED_PATHS` (which already carries a negative
  control and a substring-decoy test). Perturbation-proved: removing `docs/audits` from `ci.yml`
  alone fails exactly `test_filter_watches_path[docs/audits]` (1 failed, 18 passed); restored ->
  19 passed. Not a cost regression: `docs/audits` holds a handful of JSON ledgers.
- **Open follow-up (not yet done):** the filter is still a hand-maintained path list. The durable
  form is to derive it from what the test lanes actually read, or at minimum to add a check that
  any file loaded by a test under `tests/` is covered by `CODE_FILES`. Filed as a research item
  rather than fixed, because deriving it needs a real import/IO census, not a guess.

## Recent campaign notes (2026-08-21) - STALE-ROLLUP-AFTER-FORCE-PUSH (process hazard, no code fix)

- **Finding: `statusCheckRollup` can report check runs belonging to a head that no longer exists**,
  so a monitor keyed on the PR number reads a verdict for bytes nobody will merge. Measured: a
  monitor reported `PR #1087 TERMINAL FAILED (8) :: Formatting & Linting, test-python (6 lanes),
  test-gpu-nvidia`. Those checks belonged to `fd33dc5`, a head that had been force-pushed away; the
  live run on the corrected head `c77c0fd3` was still `in_progress`. Acting on that verdict would
  have meant debugging a failure on discarded code.
- **Rule:** watch **by run ID** (`gh run view <run-id>`), or assert the check's head SHA equals the
  PR's current `headRefOid` before believing any verdict. This is the same class as
  `--limit 1 hides the executing run`: a query returning rows about the wrong object, where every
  row is individually true.
- **Second-order note:** this bit inside a session that had already written the
  windowed-query law down. Knowing the rule did not prevent it - only keying the instrument to a
  unique object id does.

## Recent campaign notes (2026-08-21) - INLINE-RULES-DROPS-ENGINE (P1, silent misroute)

- **`tg scan --inline-rules` silently ignores a rule's `engine:` declaration.**
  `_load_inline_rule_specs` (`src/tensor_grep/cli/ast_scan.py`) parses the YAML into a rule-spec
  dict but never copies the `engine` key. Measured directly, not inferred:

      >>> _load_inline_rule_specs("id: probe\nengine: regex\npattern: SENTINEL\n"
      ...                        "language: python\nseverity: high\nmessage: m")
      parsed spec keys: ['id', 'language', 'message', 'pattern', 'severity']
      engine preserved? False

- **Consequence:** the regex fast path `if rule.get("engine") == "regex": continue`
  (`src/tensor_grep/cli/ast_scan.py` ~:792) can never fire for an inline rule, so EVERY inline
  rule -- including one explicitly declared `engine: regex` -- is routed through AST backend
  selection (`_select_ast_backend_for_rule` / `_select_ast_backend_for_pattern`,
  `src/tensor_grep/cli/ast_workflows.py`). A rule that asks for regex gets an AST backend, and on
  a machine without an ast-grep binary it fails with
  `Explicit AST search requires AST dependencies` instead of running as regex.
- **This is a SILENT misroute, which is what makes it P1 rather than cosmetic.** The user's
  declaration is accepted without complaint and then disregarded. There is no warning, no
  `engine_ignored` field, and no error naming the key -- the only symptom is a dependency error
  that mentions AST, on a rule the author explicitly said was not AST.
- **Where it surfaced:** `tests/unit/test_w1c_sarif_version_disclosure.py` failed only on Linux CI
  while passing on a Windows dev box, despite NEITHER having the `ast_grep_py` package. The
  discriminating variable turned out to be an **`ast-grep`/`sg` CLI BINARY on `PATH`** -- a
  different signal from the Python package -- which `AstGrepWrapperBackend.is_available()`
  (`src/tensor_grep/backends/ast_wrapper_backend.py` ~:95-124) probes. The dev box has the binary;
  a fresh `ubuntu-latest` runner does not. Two distinct "is AST available?" signals in one
  codebase is itself worth a look.
- **Not fixed here.** The test was made env-independent by forcing the seam (A85) rather than by
  relying on the `engine:` tag being honored, because honoring it requires a `src/` change and a
  deliberate decision about the intended contract. Someone taking this row must decide which is
  true and then make the code and the docs agree:
  1. `engine:` is SUPPORTED on inline rules -> preserve the key in `_load_inline_rule_specs` and
     add a test asserting an `engine: regex` inline rule runs with NO ast-grep present; or
  2. `engine:` is NOT supported on inline rules -> reject an inline rule that carries the key with
     a named error, rather than accepting and ignoring it.
  Silently accepting a key you discard is the one option that should not survive. Whichever is
  chosen, the RED arm must run on a machine with no `ast-grep` binary on `PATH` -- otherwise the
  wrapper backend serves the pattern and the test passes without exercising the defect.

## Recent campaign notes (2026-08-21) - RULESET-UNREACHABLE-ON-STOCK-INSTALL (P0, customer-facing, reproduced on the PUBLISHED wheel)

- **`tg scan --ruleset <builtin>` does not work on a stock `pip install tensor-grep`, while
  `tg rulesets` advertises six security rulesets with rule counts and no availability caveat.**
  This is the "capability the artifact claims and no install path reaches" class that `tg find`'s
  own `--help` already names as a dishonesty equal to a stamped-but-unpublished version -- except
  here it is on the SECURITY surface and it is the advertised feature, not a held one.
- **Reproduced on the PUBLISHED artifact, not a dev tree.** Clean `python:3.12-slim` container,
  `pip install tensor-grep==1.111.1`, nothing else:

      tg rulesets            -> lists auth-safe, crypto-safe, deserialization-safe,
                                secrets-basic (21 rules), subprocess-safe (33 rules), tls-safe
      tg scan /probe --ruleset subprocess-safe --json   -> exit 1
        Error: Explicit AST search requires AST dependencies: ast-grep wrapper backend is
        required for this pattern but is not available

  The probe directory contained a real `subprocess.call(..., shell=True)` finding, so this is not
  an empty-input artifact. The SAME command against a path that does not exist returns the SAME
  error and exit code -- the failure is unconditional, not input-dependent.
- **Why: nothing installs the backend it needs.** `ast_grep_py` appears in NO dependency and NO
  extra in `pyproject.toml` (the `ast` extra ships **tree-sitter**, which the error message itself
  says cannot serve ast-grep code patterns). And the wheel bundles no native binary --
  `tg doctor --json` in that container reports `native_tg_binary_exists: false`,
  `native_tg_binary_kind: "missing"`, and `tg` on `PATH` is the Python console script.
- **Why it looked fine from a dev box:** a developer machine with a separately-installed native
  `tg` binary serves these rules and returns `matched_rules: 1`. The capability is present for
  whoever built the native artifact and absent for whoever ran `pip install`. **Any check of this
  feature that runs on a maintainer's machine is measuring the wrong population.**
- **The sibling shows the house standard, and scan misses it.** With its optional backend absent,
  `tg find` degrades VISIBLY, still returns results (BM25-only), and names the fix:
  ``semantic ranking unavailable: model2vec not installed -- run `tg install-dense` (or pip
  install 'tensor-grep[semantic]')``. `tg scan --ruleset` hard-fails with no remediation string,
  and there is no `install-ast` counterpart to `install-dense`.
- **This also explains a CI failure that looked unrelated.** `test-python (ubuntu-latest)` on
  PR #1070 fails `test_w1c_sarif_version_disclosure` with this exact message. The Linux lanes have
  no ast-grep, so any scan reaching a code pattern errors there while passing on a Windows dev box
  -- an environment-tracking test, not a behaviour-tracking one (AGENTS.md A85).
- **Remediation options, all `fix:`-class (cannot publish while PYPI-SIZE-CAP is open):**
  1. Make the built-in rulesets work without ast-grep, or
  2. add the backend to an extra plus an `install-ast` one-shot mirroring `install-dense`, and give
     the error a remediation string naming it, and
  3. have `tg rulesets` disclose availability on the CURRENT install rather than listing rules that
     cannot run -- an advertised count of 33 rules that always errors is worse than an honest
     "unavailable here".
  Whichever is chosen, the acceptance test must run in a **clean container off the published
  artifact**, not on a machine that has a native binary lying around, or it will pass while the
  defect ships.
- **Ordering note for the SCAN-SILENT-CLEAN fix (PR #1080):** on an install WITHOUT ast-grep the
  AST-dependency check fires BEFORE the new missing-path guard, so the guard is not reached on that
  path. That does not make the guard wrong -- with ast-grep present it is exactly what fails the
  scan closed -- but a fix for THIS row should re-check the ordering so a missing path is reported
  as a missing path rather than as a dependency error.

## Recent campaign notes (2026-08-21) - SCAN-SILENT-CLEAN-ON-MISSING-PATH (P0-class honesty defect on a SECURITY surface)

- **`tg scan --ruleset` reports a CLEAN result, exit 0, for a path that does not exist.** Its exit
  code and its payload are byte-comparable between "scanned a real tree and found nothing" and
  "never read a single file". On a security-rule surface this is the false-zero law embodied in the
  shipped product: a CI gate running `tg scan --ruleset secrets-basic <path>` against a mistyped,
  moved, or wrongly-translated path reports the repository CLEAN and exits 0.
- **`tg search` on the SAME input is correct**, which is both the control proving the probe
  discriminates and the proof that a fix has a working sibling to copy:

  | command | missing path | real path containing one finding |
  |---|---|---|
  | `tg scan --ruleset subprocess-safe` | **exit 0**, `matched_rules: 0`, `total_matches: 0` | exit 0, `matched_rules: 1`, `total_matches: 1` |
  | `tg search` | exit **2**, `{"error":"path_not_found","ok":false}` | exit 0, matches |

- **Reproduction (measured 2026-08-21, installed `tg 1.110.16`; exit codes read UNPIPED, because a
  trailing `| head` reports the pipeline's status and not tg's):**

      tg scan "C:\definitely\does\not\exist\anywhere" --ruleset subprocess-safe --json >/dev/null 2>&1; echo $?   # 0
      tg search "X"  "C:\definitely\does\not\exist\anywhere" --json               >/dev/null 2>&1; echo $?   # 2

- **This is NOT a WSL-specific defect, and that reframes #90.** It was found while premise-checking
  the WSL rows, but it reproduces on a plain nonexistent WINDOWS path with no WSL involved. #90 is
  filed as "WSL raw-path scan matched_rules=0 while translated-path control total_matches=6" and is
  BLOCKED behind the Task 2A/2B typed-path program. The WSL symptom is a *consequence*: a
  `/mnt/c/...` argument is silently rewritten to `C:\mnt\c\...` (verified: `C:\mnt` does not exist)
  and then scanned as if it were real. **The underlying missing-path validation gap is independent
  of typed paths and is fixable today** without waiting on that program -- `tg search`'s existing
  `path_not_found` check is the model.
- **Measured WSL arm, for the record** (fixture: 6 files with a marker + 1 file with a
  `subprocess.call(..., shell=True)` finding):

  | arm | path tg actually used | `matched_rules` | exit |
  |---|---|---|---|
  | control, native Windows path | the real directory | 1 | 0 |
  | WSL raw `/mnt/c/...` | `C:\mnt\c\...` (does not exist) | 0 | 0 |

  The `total_matches=6` control quoted in #90's row reproduces exactly on `tg search` with the
  native path, so the historical receipt is sound; what has changed is the diagnosis.
- **Instrument note, so the next person does not lose an hour to it:** running this repro from Git
  Bash WITHOUT `MSYS_NO_PATHCONV=1` mangles `/mnt/c/...` into
  `C:/Program Files/Git/mnt/c/...` before tg ever sees it, producing a real-looking
  `path_not_found` that is the SHELL's doing, not the product's. Every arm above was run with
  `MSYS_NO_PATHCONV=1`.
- **#89 also still reproduces** (`tg search` with a raw `/mnt/c/...` path -> exit 2,
  `path_not_found`), so that row's premise is intact. Arguably that is CORRECT behaviour for a
  Windows binary given `/mnt/c/...` is not a Windows path; whether the Windows front door should
  TRANSLATE WSL paths is the actual open design question, and it belongs to the typed-path program.
  The row should say so rather than describing it as a bug.
- **Proposed fix (needs its own `fix:` PR; `fix:` cannot publish while PYPI-SIZE-CAP is open):**
  validate the scan root before scanning and fail closed with the same `path_not_found` shape and
  exit code `tg search` already uses. Required RED arm, bidirectional: a nonexistent path must exit
  non-zero BEFORE the fix is written (it currently exits 0), and a real path with a known finding
  must still exit 0 with `matched_rules: 1` after -- otherwise the guard is indistinguishable from
  breaking scan entirely. Sweep the sibling scan-family surfaces (`tg scan` with a config file,
  `tg run`, and the MCP `tg_ruleset_scan` handler) rather than fixing only the one route measured
  here: the census, not the instance.

## Recent campaign notes (2026-08-21) - FIND-JSON-CONTRACT-VIOLATION (P1, product defect, found by dogfood)

- **`tg find --json` violates the search-output contract it reuses, on two REQUIRED fields.**
  `tg find --json` returns the same envelope as `tg search --json` (same `version`,
  `routing_backend`, `routing_reason`, `total_matches`, `matches`, ... plus `schema_version` and
  `rank_fallback_reason`), but it emits **`routing_backend: null` and `routing_reason: null`**.
  Both are listed in `required` in `tests/schemas/tg_output.schema.json` and typed
  `{"type": "string", "minLength": 1}`. A contract-aware consumer either throws or mis-branches.
- **Reproduction (measured 2026-08-21 against installed `tg 1.110.16`):**

      mkdir probe && cd probe && printf 'def authenticate_user(name):\n    return name\n' > auth.py
      tg find "how does user authentication work" . --json > find.json
      tg search "authenticate" . --json > search.json

  Measured values, with `tg search` as the CONTROL proving the fields can be populated:

  | command | `routing_backend` | `routing_reason` |
  |---|---|---|
  | `tg search --json` | `"NativeCpuBackend"` | `"json_output"` |
  | `tg find --json`   | `null`              | `null`           |

  Validating the real `find` payload against the schema fails with
  `ValidationError: None is not of type 'string'` on `properties.routing_reason`.
- **Note the argument order while reproducing:** it is `tg find QUERY [PATH]`, NOT
  `tg find [PATH] QUERY`. Passing the path first makes the query be read as a path and exits 1
  with `Path not found: .../how does user authentication work`.
- **Why nobody caught it:** `tests/schemas/tg_output.schema.json` describes the primary search
  contract, but until 2026-08-21 **no test validated anything against it**. Its only references in
  the whole tree were `tests/unit/test_file_size_budget.py` (which merely counts its lines as a
  "contract" category) and `docs/code-map/tests_schemas.md` (which lists the path). A schema no
  test loads cannot fail, so it cannot be evidence. `tests/unit/test_search_json_schema_contract.py`
  now validates against it with bidirectional controls, and that is what surfaced this defect.
- **The schema also omitted the completeness triple** (`result_incomplete`, `incomplete_reason`,
  `incomplete_reason_class`) that `docs/CONTRACTS.md` makes load-bearing for agent retry
  decisions. They fell through `additionalProperties: true`, so a mistyped emitter validated fine
  on the ONE field an agent must branch on to avoid reading a truncated scan as complete. Now
  declared with types. Deliberately NOT enum-constrained: CONTRACTS.md records the vocabulary as
  "the set wired so far", so pinning an enum would make the schema REJECT a valid payload the
  first time a new cause is wired -- a worse failure than the gap.
- **Proposed fix (NOT yet applied - needs its own `fix:` PR, and `fix:` currently cannot publish
  while PYPI-SIZE-CAP is open):** populate `routing_backend`/`routing_reason` on the find route
  with real values describing the hybrid path actually taken (and the BM25-only fallback when the
  dense leg is absent), rather than `null`. Whoever takes this must decide deliberately between
  (a) populating the fields and (b) declaring that `find` is a DIFFERENT contract that merely
  resembles the search envelope - and if (b), give it its own schema and stop reusing the
  envelope's field names. Do not "fix" it by relaxing the schema's `required`/`minLength`: that
  would weaken the contract for `tg search`, which is correct today, to accommodate a caller that
  is not.
- **A regression arm was deliberately NOT added as a skip/xfail.** Adding one would have turned a
  real failure into a green-looking suite, which is the exact hazard recorded above under the
  split-baseline law. The defect is tracked here instead until it is fixed for real.

## Recent campaign notes (2026-08-21) - STACKED-PR-CI-BLINDSPOT (P1, fix available, no CEO gate)

- **Finding (2026-08-21): a pull request whose BASE is a feature branch gets ZERO CI, and the
  absence renders as "skipping".** `.github/workflows/ci.yml` filters
  `pull_request: branches: [ "main" ]` - that filter matches the **base** ref, so a stacked PR
  (base = another feature branch) never triggers `ci.yml` at all. Measured: PRs #1068 (base
  `test/w1a-mcp-handler-audit`) and #1070 (base `test/w1b-cli-handler-audit`) had **exactly one**
  check run each across their entire life - `Dependabot Automation`, conclusion `skipped`. A
  control separates this from "CI ran and passed": sibling PR #1065, same `test/` branch prefix but
  base `main`, showed `SUCCESS=39`. So it is the BASE ref, not the branch name, and not a runner
  outage.
- **Why it is dangerous rather than merely missing:** `gh pr checks` prints the row as `skipping`
  and `mergeStateStatus` reports `MERGEABLE`. Both read as benign. Two PRs carrying
  SILENT-SWALLOW error-handling hardening sat merge-ready with no test, lint, security, or
  cross-platform evidence whatsoever. This is the false-green class the instrument laws exist for:
  the check did not fail, it never existed, and absence is displayed in the same column as success.
- **Also measured: retargeting alone does not restore the signal.** `gh pr edit --base main` changes
  the base but fires `pull_request` action `edited`, which is not in the default trigger type set,
  so still no run. A close/reopen (action `reopened`, which IS in the default set) is what actually
  fires CI. Applied to #1068 and #1070 on 2026-08-21; both then produced real matrix runs.
- **Standing rule:** before merging any PR, assert `baseRefName == "main"`. A "skipping"-only check
  rollup is NOT a pass - it is an absent gate. Enumerate with
  `gh pr list --state open --json number,baseRefName`.
- **Proposed durable fix (buildable, not gated):** fail any PR whose base is not `main`. Needs a
  bidirectional control before it is trusted - a stacked PR must FAIL it and a main-based PR must
  PASS it - and note the bootstrap problem that a workflow which does not run on stacked PRs also
  cannot police them, so this likely belongs in a branch-protection ruleset or a merge-time check
  rather than in `ci.yml` itself.

## Recent campaign notes (2026-08-21) - PYPI-SIZE-CAP corrections (supersedes claims below)

- **Decision packet is ready:** `docs/audits/2026-08-21-pypi-size-cap-decision-packet.md`. Measured
  independently of the earlier entry and AGREEING with it: **713 releases, 2,847 files,
  10,733,755,391 bytes = 10.734 GB**. Three retention policies costed with real numbers; the
  recommended default (Policy A: keep the last 5 minor lines + every `X.Y.0` milestone) deletes 548
  releases, frees **8.20 GB**, and leaves roughly 439 releases of headroom. Deletion remains
  IRREVERSIBLE and CEO-GATED; nothing has been deleted.
- **CORRECTION - THREE releases are incomplete, not one.** Besides v1.111.1, `1.13.44` is missing
  its sdist (pre-existing, 2026-06-25, unrelated to the cap) and `0.1.0` predates the native-wheel
  shape. The per-artifact verification law below must therefore be applied as a sweep over all
  releases, not only the newest.
- **CORRECTION - v1.111.1 is missing the sdist too, not only the Windows wheel.** The entry below
  states "no win_amd64 wheel and no sdist", but its surrounding prose then reasons only about
  Windows. Source installs are equally affected: anyone with no compatible wheel falls back to
  v1.111.0.
- **REFUTED - stripping debug symbols did not shrink the artifacts.** PR #1067 (`5423b4b`) added
  symbol stripping on the stated expectation of a "20MB->smaller wheel". Measured against the live
  index: the last-50-release average is **18.67 MB**, identical to the last-20 average, and the
  single largest release in project history is **v1.111.0 at 19.1 MB**, published 2026-08-20 - the
  day BEFORE the incident. Whatever that change did, it bought no measurable headroom, so it must
  not be counted as partial remediation, and the cap decision cannot be deferred on the theory that
  releases are trending smaller.
- **UNVERIFIED, flagged deliberately:** the claim that `yank` does not free project space is
  consistent with well-known PyPI behavior, but it could not be confirmed against PyPI's own docs as
  a primary source. Treat as unconfirmed until checked.

- **Sequencing consequence for the merge queue (new):** while the cap is unresolved, merging any
  `fix:`/`feat:` PR bumps the version, tags, publishes GitHub release assets, and then fails or
  partially completes the PyPI upload - manufacturing another platform-skewed "latest". Release
  class is therefore part of merge eligibility right now: `refactor:`/`test:`/`docs:`/`chore:`
  PRs are safe to merge (they publish nothing under the angular default parser), and `fix:`/`feat:`
  PRs must be held until the cap is cleared. Verify the class from the COMMIT that will land, not
  the PR title, whenever a PR has exactly one commit.

## Recent campaign notes (2026-08-20) - PYPI-SIZE-CAP: release pipeline hard-blocked (P0, CEO-GATED remediation)

- **UPDATE 2026-08-21 - v1.111.1 is PARTIALLY published and that is worse than absent.**
  A publish retry got 2 of 4 artifacts up (macosx_arm64 + manylinux wheels) before the size
  cap 400'd the rest: **no win_amd64 wheel and no sdist exist for 1.111.1**. Windows pip
  resolves to 1.110.x while Mac/Linux resolve to 1.111.1 - a platform-skewed "latest".
  Verified via the PyPI JSON API (releases['1.111.1'] lists exactly 2 files). Law: verify a
  release PER-ARTIFACT (expected filename set), never by the version appearing at all.
  After the deletion run frees space, re-run the publish for the MISSING artifacts and
  re-verify the full 4-file set before any dogfood claim. Old releases still present
  (713 releases / 10.73 GB re-measured 2026-08-21); the deletion run remains the gate.

- **Finding (2026-08-20, run 32426087438):** `publish-pypi` failed with HTTP 400
  `Project size too large. Limit for project 'tensor-grep'` on the v1.111.1 publish (W2-b,
  PR #1061). Measured from the PyPI JSON API at time of failure: **713 releases, 10.73 GB
  total, ~15 MB per release** (4 native-asset wheels each) against PyPI's 10 GB project cap.
- **State it leaves:** v1.111.1 is TAGGED with GitHub release assets published
  (`Semantic Release` + `publish-github-release-assets` both green), but **no PyPI wheel**
  - the committed-not-shipped class. Every future `fix:`/`feat:` publish 400s until space
  is freed. The closeout campaign's remaining `[REL]` windows are blocked on this.
- **Remediation options (both CEO-GATED):**
  1. **Delete old release files on PyPI** (keep recent versions + pinned milestones).
     Frees space immediately; IRREVERSIBLE and user-facing (anyone pinning a deleted
     version breaks). Needs an explicit keep-list decision.
  2. **File a PyPI project-size limit increase request** (pypi/support GitHub issue,
     standard process; public, takes days-weeks). Non-destructive; slower.
  - Mitigation regardless: reduce per-release footprint (4 wheels x ~15 MB every patch
    release is the growth driver; 713 releases is the accumulation driver).
- **Verification command:** `curl -s https://pypi.org/pypi/tensor-grep/json` and sum
  `releases[*][*].size`; re-run the failed publish only after space is freed (a rerun
  before that 400s again - do not panic-rerun).

## Recent campaign notes (2026-08-14) - W5-W8 closeout: demand dispositions, CEO packets, board final sweep

- **Scope:** the W5-W8 tail of `docs/plans/2026-08-13-backlog-completion-plan.md` (council-approved
  7/7 after 5 rounds). No product code; no release; no spend. Base `a1c51ee` (v1.110.16).
- **W5 demand dispositions** (`docs/audits/2026-08-13-demand-gated-dispositions.md`):
  #255 re-derived (max single-pack anchors 35 of 129 across six never-unionable packs; no named
  100+-pattern user; both reopen arms unmet) -> LEAVE. DD-006 bounded measurement EXECUTED with a
  control-valid positive control (20 clients x 60s; single-shot control arm 20/0 meets the
  plan-frozen threshold verbatim; 5 of 5 live arms showed timeouts at the CLI's own 0.5s budget,
  the one discriminated arm classified them connect-timeout, 0 refusals/drops, 0 failures at a
  2.0s budget and single-client) -> the demand condition ("measured concurrent daemon load") is
  now SATISFIED; the row stays open with the reproduction as its trigger, mechanism hypothesis =
  default request_queue_size=5 accept backlog under concurrency. The DD-006 design packet merged as
  PR #1015; reopening product work requires deliberate authorization for the PERF + HONESTY build.
  AST-DSL-PARITY Exa delta ->
  LEAVE (peers reach for DSL/parity, not metavariable performance). MCP-LEAN-DEFAULT Exa delta ->
  direction now SPEC-LEVEL (official MCP progressive discovery / programmatic tool calling),
  still Task-2C-fenced (contract 1.7.0 re-verified). CONTINUOUS-REFRESH Exa delta -> warm serving
  demonstrably table stakes (TriSeek v0.4.2, cgh, seekr, Cursor warm builds); scoping-pass reopen
  stands.
- **A101 recurrence receipt (W8 acceptance):** `public-version-powershell` flaked **3 times in 3
  consecutive windows-agent-readiness runs** at `timeout_s=30` before #1009 (v1.110.15) shipped
  the bounded timeout-retry fix; the third recurrence was the structural-fix signal, per A101.
- **W6 blocked rows:** six rows, six commands, six recorded results (#89/#90 parked-Task-2A,
  F5/F6/F8 shared-box ban, MCP-SURFACE 1.7.0 fence); per-row receipt table in
  `docs/audits/2026-08-13-demand-gated-dispositions.md`. Zero status flips.
- **W7 CEO packets** (`docs/audits/2026-08-13-ceo-gated-packets.md`): 2026-08-13 deltas on the
  2026-08-06 base; 8-seat thinktank (7 verdict-bearing, copilot TIMEOUT failed-seat, claude seat
  substituted sonnet for quota-blocked Fable 5) -> 7/7 HYBRID-ACCEPTED / ADVISORY-ONLY; council
  named the concrete seams (frozen: `~/.tensor-grep/bin/tg.exe` order, installer,
  `rust_core/src/main.rs`; rejected: ledger blocking-gate / exit-code path; accepted: ADR +
  benchmark cold-start stamp; advisory overlap hint + exit-code pin test). #169 pointer only, no
  recommendation. Five sections each carry the literal `STATUS REMAINS CEO_GATED` terminator.
- **W8 board sweep:** all five demand-row triggers refreshed from W5 receipts; DD-006 trigger
  carries the reproduction; closed world re-derived = 29 rows (GATE-W8-2). Index stays
  `2026-08-13.1`. Raw probe evidence: `artifacts/dd006_*.json` (probe is scratch-only,
  `.orchestrator/w5/dd006_probe.py`, not committed to `src/`).

## Recent campaign notes (2026-08-14) - W5-W8 closeout + session capture

- **Session-capture receipt:** everything learned this session (shipped state, DD-006
  measured method + run matrix, codex gate rounds, council seat accounting, Exa deltas,
  A111-A116 provenance, follow-ups) is captured in
  `docs/audits/2026-08-14-session-capture.md`, written so a junior analyst can pick it up.
- **Codex 4-round audit receipt:** gpt-5.6-sol audited the docs branch in four rounds -
  R1 5 findings (control threshold, undifferentiated timeout claim, missing W6/A101
  receipts, untracked plan) fixed; R2 2 LOW (plan ASCII-census falsehood, trailing space)
  fixed; R3 1 LOW (census location inventory) fixed; R4 APPROVE on `7b7f3c8`. The
  round-by-round closure and the plan hash chain survive in the disposition doc's
  Codex-audit-closure section (the raw reports were scratch in the removed
  w8-docs-closeout worktree).
- **Thinktank seat accounting:** 8 seats, 7 verdict-bearing, 7/7 HYBRID-ACCEPTED /
  ADVISORY-ONLY; copilot TIMEOUT recorded as a FAILED seat, not a blocker; the claude seat
  sat `sonnet` because Fable 5 was quota-blocked at dispatch (substitution recorded in the
  synthesis header + CEO packet doc, never presented as a Fable verdict).
- **Landed skill:** `tensor-grep-demand-gate-measurement` documents the bounded demand-gate
  measurement method with the DD-006 worked example (frozen thresholds, single-shot vs looped
  control arms, discriminated failure classes, positive control, CANNOT_MEASURE, honest-soft
  severity). The docs-artifact audit-loop learnings FOLD into the existing
  `tensor-grep-codex-gated-audit-loop` skill (FOLD_INTO_EXISTING bias; no second new skill).

## Recent campaign notes (2026-08-13) - session-retention campaign: 35/35 skill accuracy audit + never-committed lesson capture

- **Scope:** independent accuracy audit of ALL 35 tracked `.claude/skills/*/SKILL.md` (7 waves,
  artifact-specific receipts) + capture of the 2026-08-12 campaign's lessons across docs/AGENTS/
  skills/workflows/tools/paper. Base `568065a` (v1.110.14). No release; no spend.
- **Result:** 7 CLEAN / 28 DRIFT_FOUND / 0 CANNOT_VERIFY; zero new skills justified
  (fold-over-fragmentation per the Exa "coherent unit" guidance). Every HIGH/MED substantive
  drift repaired with file:line-verified edits (MaxSim HELD→RETIRED; TG_FIND_DENSE_WEIGHT
  default-OFF→adaptive-5.0-shipped; Battle 29/28/20/23 statuses; junction-rule mechanism;
  worktree-store overclaim; installation_health enum; exit-2 wording; MCP 5th registration site;
  ~20 more). Dated receipts SUPERSEDED/annotated, never rewritten; drifted anchors converted to
  grep-the-symbol form, never re-stamped bare.
- **Never-committed capture:** the dirty `audit/h6-cudf-backend` tree carried the 2026-08-07
  Session Lessons + CI Cost Discipline sections that were never committed to any ref
  (pickaxe-verified); landed verbatim into AGENTS.md + SESSION_HANDOFF.md with provenance.
  Reconciliation carries ERRATUM-2 (the one-file spot-check that misclassified them as stale).
- **Tooling:** `tg-skill-audit.js` hardened (artifact binding: root/SHA/blob manifest; exact
  coverage equality; evidence floor; CANNOT_VERIFY); `tg-audit-fix-loop.js` five advertised phases
  wired (Seam/RED/GREEN/Gate/Verify) with FIX-FIRST verdict vocabulary; new
  `tests/unit/test_skill_rules_registry.py` (schema + regex-compile + dangling-key governance).
- **Gates:** 117 governance tests passed in the real venv; ruff + mypy clean on the new test;
  both workflows pass a wrapped `node --check`. Independent adversarial gate FIX-FIRST (8) →
  repaired → re-gate SHIP-WITH-NITS. PR CI: one known `public-version-powershell` flake,
  rerun green.
- **Landed:** PR #1005 (`docs:`), commit `f7bcc9a`, squash-merged `5148664` (2026-08-13).
  Receipt: `docs/audits/2026-08-12-session-retention-audit.md`.
- **Lessons retained (2026-08-13 follow-up):** the campaign's own failure modes became **A97-A102**
  (interrupted-edit-may-have-applied; spot-check-census-of-N-files; verifier-must-be-artifact-bound;
  advertised-capability-must-execute; third-flake-=fix-signal; input-brief-facts-are-hypotheses),
  folded into `tensor-grep-docs-and-writing` / `-validation-and-qa` / `-change-control` /
  `-debugging-playbook`. The fresh-context adversarial gate that caught these is the A18/A29
  discipline re-confirmed (author self-verification reported zero; the independent gate found 8).

## Recent campaign notes (2026-08-12) - backlog-closeout campaign: Task 2A resume + reconciliation + research receipts

- **Plan:** `docs/plans/2026-08-12-backlog-closeout-campaign.md`, rev 6 - 6 revisions through a
  3-seat council (ground-truth all-TRUE; adversarial seat CHANGES_REQUIRED->APPROVED; codex Sol
  seat 4 rounds `REVISE H1-H3/M1-M4` -> `REVISE M5-M8` -> `REVISE M5,M6` -> **APPROVE** on hash
  `892B0B70...BC3FFF`). 21 findings folded; every finding verified against the artifacts before
  folding (codex census corrections H1/H2 re-derived by parsing the manifest + re-running the
  baseline).
- **Task 2A resumed (#966, #89/#90 program):** A22 union-MERGE of `b6dc0a6` into
  `task2a-round60-red` (only `cli/main.py` textually conflicted; the union preserves main's H5
  timeout contract AND the branch's A62 emit-after-start - 5/5 + 158-node 0-delta per-node
  oracle). PR #966 flipped CONFLICTING->MERGEABLE and got its FIRST-EVER Actions runs. Five
  repair rounds landed off real CI receipts: `mod tests` closing brace (unclosed since the
  `c550a84` scaffold - the branch never compiled anywhere; A87), `raw_args` E0282, lib-test
  imports in `python_sidecar` tests (11x E0425/E0433) + a 14-error ruff sweep, and R1 - the
  blanket `Run Pytest` step now excludes the 4 census-owned suites so the Task 2A Windows
  collector is REACHABLE (codex H3; RED-proven 3-arm ratchet: bidirectional
  ignore-list<->manifest, collector order/condition, live-collection<->manifest closed world).
  Live census corrected to **169 nodes (157 py + 12 rust)**; the #9d receipt is demoted to
  historical (ERRATUM-1). **Still NOT GREEN:** Sol exact-byte SHIP + Windows census evidence
  outstanding; board stays BLOCKED. Receipts:
  `docs/audits/2026-08-12-stale-branch-reconciliation.md` sections 3-4.
- **Stale-checkout reconciliation:** the main checkout's `audit/h6-cudf-backend` branch + 21
  dirty files contain ZERO unlanded product work (H3/H6 shipped; blob-identity receipts in the
  audit doc). The `nul` artifact was removed; all other cleanup PROPOSED only.
- **Research receipts** (`docs/audits/2026-08-12-research-receipts.md`, 17 Exa queries, 47
  sources): frontier scan CONFIRMS the edit-control-plane thesis (retrieval commoditizing;
  receipts/verification is where 2025-26 activity is; nearest neighbor arXiv:2606.04193 is
  generic-agent, not repo-edit - whitespace remains). Demand rows: #255/DD-006/AST-DSL-PARITY
  LEAVE (receipts in row triggers); MCP-LEAN-DEFAULT direction-confirmed but Task-2C-fenced;
  CONTINUOUS-REFRESH scoping-pass trigger; **RUST-REPLACE-SYMLINK flipped DEMAND_GATED->READY**
  (2026 CVE class: sed CVE-2026-5958, uutils GHSA-239g-2685-54x3, Capgo CVE-2026-56236;
  design-council pass required before build).
- Board: canonical index `2026-08-12.1` - 17 unfinished = 1 READY, 6 BLOCKED, 5 CEO_GATED,
  5 DEMAND_GATED; 0 IN_FLIGHT. No release this campaign; no spend; CEO gates untouched.

## Recent campaign notes (2026-08-11 late) — skill coverage wave (post-#1001)

- **Post-merge coverage audit** of the 34-skill library vs the A83-A96 / M16-M17 / doctor-3 / world-class wave: 6 gaps found (A87 static-SHIP-provisional, A89 real-artifact parity, A92 escrowed evidence, A96 byte-exact-edit, M16 scan rule preservation, world-class roadmap).
- **Five lesson-gaps folded into existing skills; one NEW skill: `tensor-grep-worldclass-roadmap`** (S1-S7 edit-control-plane spine, Exa-grounded: Occasio OIDC attestation / AET evidence-freshness / Anthropic harness papers). Index 33 -> 34; skill_rules.json 22; workflow 35/35; gates 16/16.
- Laws A94-A96 were captured in #1001; this wave converts the remaining audit finding into skill coverage so `tg-skill-audit` and future sessions find the disciplines without re-deriving from AGENTS.md.

## Recent campaign notes (2026-08-11 PM) — CEO update to v1.110.14

- **Public product: `v1.110.14`** (tag, GitHub assets, PyPI). Since the 2026-08-10 snapshot: v1.110.11 (M16 #987), v1.110.12 (M17 #988), v1.110.13 (A90 #997 unknown-command fail-closed), v1.110.14 (doctor #1000 PATH honesty: pypi_latest / installed_behind_pypi / shadow_launchers / installation_health) + audit fixes H2 #979, M1 #982, M3 #983, M14 #984 + spend-smart CI #977.
- **Skill-library evolution (#999 #1001 #1002):** dogfood refreshes + full-library audit (21 stale stamps, 7 tier contradictions) + coverage-gap wave (worldclass-roadmap skill, five skills extended). All post-merge verified.
- **Closed world unchanged:** 28 rows / 17 unfinished (0 READY, 6 BLOCKED, 0 IN_FLIGHT, 5 CEO_GATED, 6 DEMAND_GATED). No new rows; no status flips. Open PRs: only #966 (Task 2A parked). No spend; #169 only money stop. No nonfinancial CEO question.
- **CEO packet (live):** `docs/audits/2026-08-11-ceo-backlog-update.md`.
- **Next after the next code release:** run the `tensor-grep-release-drift-check` sweep (A94) before further skill work.

## Recent campaign notes (2026-08-11) — skill-library audit + freshness mechanism

- **Skill-library audit (all 33 in-repo `.claude/skills/*/SKILL.md` files, 3 parallel subagent waves):**
  library found stale ONE release after the last refresh — 21 version stamps below the v1.110.14
  current tag, 7 language-tier contradictions (foundational-vs-parser-backed surviving the C/C++
  promotion; ground truth now 10 parser-backed / 0 foundational via `_symbol_navigation_descriptor()`),
  2 stale state facts (M17 index-fingerprint, doctor schema-3), 1 dangling prose contradiction.
- **Fixes:** mechanical stamp bumps (generated edit scripts, byte-safe); append-only dated SUPERSEDED
  blocks in `code-search-and-retrieval-reference` + `tensor-grep-add-language` for the retired tier
  claims; doctor-3 fields (`pypi_latest`/`installed_behind_pypi`/`shadow_launchers`/
  `installation_health` + `TG_DOCTOR_OFFLINE`) added to `tensor-grep-config-and-flags` +
  `tensor-grep-diagnostics-and-tooling`; index count re-derived 32 → 33 (new folder) in AGENTS.md +
  CLAUDE.md; `.claude/skill_rules.json` now 21 entries.
- **Standing mechanism created: `tensor-grep-release-drift-check` skill** — mechanical post-release
  governance sweep (stamps ≥ current tag, derived counts, known-state facts, SUPERSEDED discipline).
  Deliberately NOT a pytest (numbers drift by design; a hard gate reddens every PR) — a maintenance
  command like `.claude/skill_anchor_audit.py`. Run it after every release.
- **Laws A94–A96** captured (stamp rot is a maintenance sweep; a "verified correct" note is part of
  the contract it guards and must be updated in the same change; non-ASCII punctuation defeats
  byte-exact edit-tool matches — splice by line index from a python script).
- **Ledgers:** `docs/audits/2026-08-11-skill-audit-findings.md` (27-item fix list +
  new-skill decision, Exa-grounded) and `docs/audits/2026-08-11-skill-audit-facts.md` (ground truth).

## Recent campaign notes (2026-08-10) — M16/M17 first-CI-row drain (plan Round 3)

- **Drain landed:** #993 (docs world-class roadmap), #994 (docs A90–A93), #992 (docs 24h capture,
  incl. A87–A89; one `ruff format --preview` lint fix on `test_skill_library_drift.py`; rebased onto
  post-#993/#994 main with union-resolved conflicts), **#987 (M16 Rust scan composite/severity →
  v1.110.11)**, **#988 (M17 index root/format → v1.110.12)**. Open at session end: #966 (Task 2A RED
  scaffold, parked by design). Full matrix green on every merged head; PyPI verified serving
  1.110.11 + 1.110.12 (4 files each, cache-bypassed). #993/#994/#992 are docs (no release); #987 and
  #988 serialized one-per-publish.
- **FINDING (A87 made real, twice):** both Rust PRs (#987/#988) had "passed" codex static audits,
  then the FIRST real CI run found genuine compile errors — #988 survived three audit rounds and
  still failed E0599/E0308/E0382 on first compile; #987's regression only surfaced on the full
  matrix (its author's self-gate never ran `tests/unit/test_backend_bug_fixes.py`). The first-compile
  gate IS the Rust gate; static SHIP is not durable until cargo builds and the matrix runs (A87).
- **FINDING (first-CI regression: fingerprint vs walk disagreement, #988):** the M17-added
  `compute_tree_fingerprint` ran RAW `read_dir` over the canonical root, so a gitignored file added
  after build flipped the digest and falsely reported staleness — disagreeing with the new-file walk
  that correctly ignores it (the code's own "walkers must not disagree" doctrine). Fix: derive the
  fingerprint population from `ignore::WalkBuilder` with the SAME config as `collect_file_entries`
  (`hidden`, `git_ignore(!no_ignore)`, `max_depth(1)`, add_ignore trio), `file`-only before sort/cap,
  and thread `no_ignore` through all call sites. The two `staleness_new_file_scan_honors_root_gitignore_*`
  tests had passed on main (no fingerprint there) and were the honest REDs.
- **FINDING (Windows-only test hid a format-pin break, #988):** `test_tg_search_index_old_format_triggers_rebuild`
  is `#![cfg(windows)]` — the M17 wire-format bump 4→6 (canonical root, then tree_fingerprint) broke
  its hardcoded `rebuilt[4] == 4` assertion on Windows only; all other legs ran the same branch and
  passed, so the break was invisible until the Windows leg ran. Fix: export `INDEX_FORMAT_VERSION`
  `pub` and pin the test to the constant (next bump cannot silently re-break). The `#![cfg(windows)]`
  gate is a platform-divergence hiding-spot worth a census.
- **FINDING (ratchet caught the new walk, #988):** `test_known_discard_sites_never_grow` (#276
  walk-error-discard ratchet) redded because the new fingerprint walk used the silent
  `.filter_map(|e| e.ok())` idiom — exactly the class the ratchet exists to forbid. Fix: log the
  discard (`map_err(|e| eprintln!(...)).ok()`, the shape the ratchet's own oracle test blesses) and
  keep the test-fixture `read_dir` enumeration on the non-ratcheted binding. Lesson: every new walk
  site must join the #276 doctrine (log, never silent) even in a best-effort staleness signal.
- **FINDING (comment satisfies the census — reversed):** the initial fix rewrote the fixture comment
  TO CONTAIN the literal `.filter_map(|e| e.ok())`, and the raw-text census counted the comment as a
  site (count stayed 2). The census regex is text-based; never write the ratcheted idiom in a
  comment near a ratcheted file (mirror of the "census satisfied by a comment" trap, reversed).
- **Codex rounds:** #987 fixture-first fix — clean. #988 — R1 FIX-FIRST (fingerprint select-FILES-
  first before cap; empty-dir + cap-displacement + symlink regressions) → R2 FIX-FIRST (cap test's
  `zdir*` sorted AFTER the target, so it passed on the bug; renamed to `adir*` + Unix-gated symlink
  arm) → R3 SHIP → final-head SHIP (one LOW: `docs/routing_policy.md` hardcoded format 4; folded
  in-PR). Independent-gate SHIP was re-earned after every CI-surfaced fix (A18).
- Execution plan: `docs/plans/2026-08-10-backlog-completion-plan.md` (2-seat thinktank-approved
  Round 3: codex-sol + agy unanimous APPROVED, no MUST-FIX). Next buildable per plan: none — the two
  Rust rows are closed; remaining rows are CEO/demand/research-gated (see header snapshot).

## Recent campaign notes (2026-08-08)

- **Drain landed:** #975 (M7 verify_receipt never-raises → **v1.110.6**), #976 (M8 AST -v/-w
  fail-closed → **v1.110.7**), #980 (TASK_BOARD reconcile → unblocked the meat-gate), #967 (docs
  A77–A82 retention), #977 (spend-smart CI gate). #978 plan PR + #979 P5·H2 draft PR open.
- **Published-wheel dogfood (v1.110.7):** M8 PASS (visible CPU fallback honors `-v`, rg-identical
  inverted set, correct JSON envelope); M7 PASS (corrupt embedded key → structured `valid=False`,
  exit 1, no crash); H2 pre-fix baseline confirmed (`--json -l` → raw path exit 0 on the wheel; the
  fix is #979, unmerged).
- **FINDING (stale-ready labels):** #967 (docs) and #977 (ci) were labeled ready/mergeable but their
  heads predated #969–#976 — each had 7 failing tracker-freshness jobs that were base-staleness, not
  content. Both rebased onto real main, re-CI'd green, then merged. Charge: any "ready" label must
  cite the head SHA's own completed run (A44/A51).
- **FINDING (release-gate incident):** v1.110.7 (#976) could not publish because TASK_BOARD's
  reconcile stamp lagged 6 releases (tolerance 5) → `test_task_board_freshness` failed in all 7
  test-python/gpu jobs → the matrix gate blocked the release train. Fixed by #980 (board reconcile
  + SESSION_HANDOFF index sync), which re-ran Semantic Release and published the orphaned M8 fix as
  v1.110.7. The freshness gate is ordinal-CHANGELOG-distance (A76) and its mirror
  (`test_backlog_tracker_truth` index-version equality) must ride the same commit.
- **H2 residual (recorded, not fixed):** `--format rg --json -l` keeps rg-parity raw paths (rg
  itself emits plain paths for `--json -l`). Tracked as a named follow-up; #979 deliberately does
  not refuse it.
- Execution plan: `docs/plans/2026-08-08-backlog-completion-plan.md` (three-lens thinktank-approved
  Round 3). Next buildable per plan: M1 (checkpoint create-side symlink/junction containment).

## Recent campaign notes (2026-08-08 late) — audit-fix wave receipts

- **Buildable audit fixes TDD'd this wave (all draft PRs, codex-gated):**
  - P5·H2 (#979): fail-closed refusal of `--count-matches`/`--files-with-matches`/`--files-without-match`
    on the native structured + positional GPU routes (compile-exhaustive 68-field ratchet; `-o`/`-c`/
    `--format rg --json` stay honored). Codex R1(4)→R2(2)→R3(1)→R4(1)→R5 APPROVE-WITH-NITS.
  - M1 (#982): checkpoint create-side junction/symlink-ancestor containment — parent-chain-only resolve,
    raw-leaf identity (A38), Windows junction fixtures (no privilege needed), 4 new tests RED→GREEN;
    A48 handle-anchoring + undo leaf-following recorded canonically as M1-FU1/M1-FU2 (owners +
    reopen triggers). Codex R1 FIX-BEFORE-MERGE(4)→R2 SHIP.
- **Named M-follow-up rows (A49, recorded beside M1-FU1/M1-FU2 in this same campaign note):**
  - **M16-FU1 `SCAN-ALL-NOT-SAME-NODE`** — `all:`/`not:` composite rule bodies (nested/intersection
    shapes) stay DROPPED fail-closed today (both the Rust twin `backend_ast_workflow.rs:1181-1184`
    and Python `ast_workflows.py:475-478` release only `any`-of member semantics; an intersection
    body requires same-node semantics the native matcher cannot express and would be
    under-matched, not served). OWNER: M16 change-control (Rust `tg scan`). DISPOSITION: DEFERRED,
    not claimed. REOPEN TRIGGER: a consumer configuration whose composite rules use
    `all:`/`not:` bodies and needs them evaluated (currently they drop fail-closed rather than
    under-match).
  - **M17-FU1 `INDEX-FINGERPRINT-SAMPLE-CAP`** — the `tree_fingerprint` (u64 full-content digest of
    the top-32 top-level files, index-machinery namespace excluded) closes same-path
    metadata-preserving swaps only for the sampled files; files NOT sampled (33rd+ top-level and
    every non-top-level file) are covered only by the per-file mtime/size identity loop
    (`rust_core/src/index.rs` `compute_tree_fingerprint` / `staleness_reason` — the honest boundary
    is named in the code as "tracked as follow-up M17-FU1"). OWNER: M17 index change-control.
    DISPOSITION: DEFERRED, not claimed. REOPEN TRIGGER: a same-path metadata-preserving swap
    landing in an unsampled file (below the 32-file sampling cap) that the mtime/size loop cannot
    detect, or a consumer requiring full-content identity for every entry.
  - M3 (#983): LSP `documentChanges` CreateFile/RenameFile/DeleteFile confinement — was VACUOUS
    (file-ops invisible; `all()` over empty set). Five-field enumeration + opaque-member fail-closed +
    strict external-DocumentUri validator (absolute file: URI only; rejects %00, whitespace,
    path-rootless `file:C:evil`, non-file schemes). Relay-only TOCTOU documented. Codex
    R1(3)→R2(3: kind-null, snake_case bypass, file:/ RFC-8089)→R3(1: path-rootless)→R4 seat FAILED on
    content filter (A10/A74 — substitute probes verified; draft-PR gate + CI are the durable arbiter).
    LSP-EDIT-CONSTRUCTION (lsprotocol documentChanges construction) named as separate tracked item.
  - M14 (#984): MCP `mcp_contract_version` central-const hard-assign (was setdefault → tool's own
    stale/forked literal won) + LIVE-registry VALUE ratchet over all 58 tools × success+error
    families; 11 tools' masked success paths wrapped; `schema_version` stays setdefault so doctor's
    v2 survives (harness_api-pinned); const untouched (1.7.0). Census corrected the "15/58 approx"
    to a live 19 sites / 11 tools. Codex R1(3)→R2(3 harness: exception-allowlist masking,
    env-dependence on dense model, partial-key parity)→R3 SHIP (independent mutation re-verified).
- **Session receipts (2026-08-08):** origin/main `5500b88`; v1.110.6 + v1.110.7 published (PyPI
  verified); open at session end: #979/#982/#983/#984 drafts + #966 (Task2A RED, parked by design).
  Remaining per plan: M16 (Rust `tg scan` composite rules + severity — CI-oracle), M17 (index stored-
  root check — CI-oracle), then research R1–R8 (each design-council-gated). All shared-box cargo and
  MCP-native ext tests remain CI-oracle (worktree venv limitation; failures proven pre-existing on
  origin/main).

## Recent campaign notes (2026-08-06 PM CEO)

- Live CEO packet: `docs/audits/2026-08-06-pm-ceo-backlog-update.md` (A77–A82).
- Closed-world index `2026-08-06.3`: **0 READY**, 6 BLOCKED, 5 CEO_GATED, 6 DEMAND_GATED
  (17 unfinished / 28 rows). Morning file retained for A70–A76 + pre-stamp READY receipt.
- Task 2A: draft #966 FIX-FIRST only — tip under review, not archaeological RED alone (A80/A81).
- Findings this wave: stdin+heredoc poller false ALL_TERMINAL (#963); usage-limit seats FAILED;
  status-stamp must retarget tracker pins (#964); AMEND_SPINE held (no MCP/F5–F8 product builds).


## Recent campaign notes (2026-08-13, PR #1010 in flight)

- `RUST-REPLACE-SYMLINK` READY -> IN_FLIGHT with PR #1010 (A50): fail-closed symlink_metadata
  guard, root refusal, junction REFUSE (GATE-W3A-1 (a), bounded toolchain probe), residual-race
  characterization pin. New canonical row `RUST-REPLACE-TOCTOU` (DEMAND_GATED) filed in the same
  PR -- it owns the leaf race, walk-time child swap, and non-leaf ancestor bypass; candidate
  machinery: `rust_core/src/safe_write.rs`'s O_NOFOLLOW / FILE_FLAG_OPEN_REPARSE_POINT.
  Threat model: `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md`.

## Recent campaign notes (2026-08-13 campaign closeout)

- Plan `docs/plans/2026-08-13-backlog-completion-plan.md` council-approved 7/7 after 5 rounds.
- PRs merged: #1008 (W1 premise receipt), #1009 (W2 A101 probe retry -> v1.110.15), #1010
  (W3 RUST-REPLACE-SYMLINK symlink/junction guard -> v1.110.16, Merged SHA d31a051).
- A3 adversarial gate: 13 opus rounds converged SHIP; codex security audit cleared. Junction fact
  settled by bounded pinned-toolchain probe (A107); RUST-REPLACE-TOCTOU row filed for the residuals.
- W4 Task 2A: Sol re-audit 9/10 HIGH fixed; round-1 F1 untyped-JUnit fix pushed to #966 head
  1210d8e; parked with receipt (union-merge onto current main is the next Task 2A action).
- Laws A103-A110 retained in AGENTS.md + CLAUDE.md + skills. Board index 2026-08-13.1.

## Recent campaign notes (2026-08-06 CEO)

- CEO dumbed-down packet: `docs/audits/2026-08-06-ceo-backlog-update.md`.
- Closed-world after READY∩BLOCKED stamp (index `2026-08-06.2`): 28 / 17 unfinished
  (**0 READY**, 6 BLOCKED, 0 IN_FLIGHT, 5 CEO_GATED, 6 DEMAND_GATED).
- F7 / CPU-BACKEND / REF-CALL-REGISTRY → SHIPPED (impl already on main; closure #963).
- Laws A70–A76 retained in AGENTS.md / MEMORY.md / skills.
- Closeout plan: `docs/plans/2026-08-06-enterprise-backlog-closeout-plan.md`
  (Round-1 AMEND_SPINE absorbed; Round-2 orchestrator SHIP substitute — Opus/Sonnet quota until 2026-08-14).
- R0 packets: `docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`,
  `docs/audits/2026-08-06-demand-gated-research-receipts.md`.


## Recent campaign notes (2026-08-06)

- **W5 published-wheel dogfood (`1.110.0`):** PASS on prepare / search± / evidence emit / signed emit /
  review-bundle create+verify(`--min-receipts 1`) / wrong-`--against` RED / ledger claim+list.
  Full verdict table: `docs/audits/2026-08-06-enterprise-w5-dogfood.md`.
- **Instrument note:** `--sign` with no key only fails closed when the default
  `~/.tensor-grep/keys/evidence_ed25519.key` is absent; ambient operator keys pollute the NEG arm.
- **#958** merged `65d0195` — enterprise prepare→evidence→review-bundle CUJ integration lock on `main`.
- **#963** merged `9bf38c2` — CEO update docs. **Finding:** squash-merge fired while ~10 PR checks were
  still `pending` because a poller treated empty stdin counts as ALL_TERMINAL (broken pipe into a
  heredoc). Recovery: watch main push CI run `31105687641` to `completed` before further merges.
  Lesson candidate: never parse `gh pr checks` through a heredoc that consumes stdin; require
  heavy-lane presence by name/count (A43).
- Still STOP for product builds: W3 rust/e2e, W4 Task 2A, MCP-SURFACE (before Task 2C), #169, CEO_GATED.


## Current canonical closeout queue — status index `2026-08-03.3`

`docs/TASK_BOARD.md` owns the machine-parsed rows. This is the human-readable mirror; older sections
below are historical evidence and do not override these dispositions.

### CORRECTION (2026-08-24): the "owned by #89/#90" framing below is STALE, do not merge this branch as a fix for those rows

A 4-agent parallel analysis of `task2a-round60-red` (the branch PR #966 was built from, retained on
origin since 2026-08-13, RED by design, never merged, PR closed as stale by the CEO 2026-08-20) found:

- **Content mismatch:** the branch's actual ~15,000-line diff (26 files) is Windows installer/trust
  hardening -- `SearchInputLedger` input-cap admission control, `_win32_path_domain.py` (Job Object
  containment, Authenticode/WinTrust chain checks, CNG signing, SDDL/DACL parsing), an
  `InstallerShimReceiptV1` schema, and a `NativeCiReceiptV1` CI-evidence-escrow schema. Nowhere in
  this diff is there WSL-to-Windows path translation code or a test for it. The "nine Round-60
  blockers owned by #89/#90" list immediately below never mentions WSL, `/mnt/c`, or path
  translation either -- it is entirely about installer authority, Job containment, and CI receipt
  integrity. The `#89`/`#90` ownership label appears to be inherited/copied attribution, not a
  description of matching scope -- the same failure mode already caught this session on the F8 and
  MCP-SURFACE backlog rows (both cited stale/nonexistent dependencies).
- **RED cause (verified by actually running the suites, not assumed):** a genuine mix -- (a) a real,
  narrow wiring gap (`SearchInputLedger.on_public_route_entry` is defined and instantiated but never
  called from `bootstrap.py`/`main.py`'s route entry points), (b) Windows-native security primitives
  (Job Objects, CNG, WinVerifyTrust) that fundamentally cannot be proven from this WSL/local sandbox
  and need a real Windows CI runner, and (c) a handful of narrow logic bugs. This is a substantial,
  mostly-real implementation deliberately left RED, not empty scaffold -- but "mostly real" does not
  mean "ready," and none of the remaining work is about #89/#90's WSL blocker.
- **Rebase cost, if this scaffold is ever resumed on its own merits:** cheap. `git merge-tree`
  dry-run against current main shows exactly 3 real conflicts (`.github/workflows/ci.yml`,
  `rust_core/src/native_search.rs`, `tests/unit/test_cli_atomic_writer_ratchet.py`) out of 26 touched
  files; the rest auto-merge clean despite main's unrelated 297-file drift since divergence
  (the 2026-08-20 giants-split). Rebase cost is NOT what is blocking this branch.
- **The `fix/wsl-path-domain` branch** referenced in `docs/audits/2026-08-13-stranded-work-premise-
  recheck.md` as a possibly-more-relevant alternative does not exist on `origin` (confirmed via the
  GitHub API branch list, 2026-08-24) -- that lead is dead too.
- **Recommendation:** do not merge `task2a-round60-red` to close #89/#90 -- it was never going to.
  #89/#90 remain genuinely blocked on a real WSL host and need a fresh, correctly-scoped plan written
  against that actual blocker. Whether the installer/CNG/Job-containment hardening this branch
  actually contains is still wanted product work is a separate, open prioritization question this
  correction does not answer -- it has had zero demand signal recorded anywhere in this backlog
  outside of its own RED-scaffold receipt doc.

### Task 2A plan gate — nine Round-60 blockers owned by #89/#90; current RED is FIX-FIRST

Round-60 plan blockers (still required by the approved plan):

- protected, fixed ProgramData installer-state authority with a bound non-exportable CNG signature;
  PATH and install-command digests never authorize a receipt;
- transacted-registry PATH mutation (`CreateTransaction` plus transacted open/write/commit) or
  fail closed, with no abstract lock/CAS fallback;
- opened directory volume/file identity before removing case, 8.3, extended-path, separator, or
  junction PATH aliases;
- exact offline WinTrust flags, Microsoft-root chain policy with test roots disabled, and a maintained
  production-root thumbprint allowlist; same-Organization foreign roots must fail;
- kill-on-close Job containment with both breakaway flags and `CREATE_BREAKAWAY_FROM_JOB` absent;
- one no-refund search-input ledger installed before every bootstrap/full/native/rg/sidecar route,
  including fail-closed uninstrumented PCRE2;
- independent inclusive cap−1/cap/cap+1 REDs for per-file and combined pattern/ignore budgets so
  split counters and off-by-one rejection cannot survive;
- `NativeCiReceiptV1` identity re-derived from the live Actions/artifact context rather than trusted
  from receipt JSON; and
- JUnit plus stable-Rust node-census cross-checks bound to that same current-run tuple.

Current local RED artifact `6367614960327b1a4e00301c8bfdb9b2e4bb453e` is Sol `FIX-FIRST` with ten HIGH
scaffold/oracle blockers (immutable-SHA CI absent; crash-as-RED; hardcoded PCRE2 oracle; forgeable
Job heartbeat; unproven Job cleanup; weak SDDL; invalid CNG export; TxR close ownership; producer
self-attest; `-f`/`--file` unbounded pre-ledger read). Not merge-ready. These are plan-gate /
RED-scaffold findings, not separate shipped features or extra canonical top-level rows.

### EXTERNAL / Phase 0+1 launch receipt — 2026-08-06 (Packet F)

Docs-class reconcile after merges **#953** (F10/DD-004), **#952** (PHP), **#955** (C#),
**#956** (`workspace_root_refused`) on `main`. Hard stops held: no #169, no Task 2A GREEN, no
local `rust_core` cargo, **no CEO-gate flips**, no `world_class_readiness` claim (no fresh
evidence packet for that label).

| packet | PR | result |
|---|---|---|
| A drain #951 | [#951](https://github.com/oimiragieo/tensor-grep/pull/951) | MERGED (squash) after full CI green |
| B F7 wave 2 PHP | [#952](https://github.com/oimiragieo/tensor-grep/pull/952) | MERGED — `use`/namespace confirmation + blast_radius_floor |
| C F7 wave 2 C# | [#955](https://github.com/oimiragieo/tensor-grep/pull/955) | MERGED — namespace/`using` confirmation + blast_radius_floor |
| D workspace_root_refused | [#956](https://github.com/oimiragieo/tensor-grep/pull/956) | MERGED — class/code params on `_emit_broad_scan_refusal` |
| E F10 + DD-004 DROP | [#953](https://github.com/oimiragieo/tensor-grep/pull/953) | MERGED — RETIRED with receipts |
| F board reconcile + dogfood | this entry + TASK_BOARD + #961 | dogfood: `tg search needle C:\dev\projects --json` against main → exit 2, `incomplete_reason_class=workspace_root_refused`, `error.code=workspace_root_refused` |

**What shipped (honest claims only) — as of this 2026-08-06 receipt:**
- F7 Task 11 waves 1–2: Java (#950), PHP (#952), C# (#955) cross-file caller confirmation on
  `main`. (Wave 3 C/C++ **#957** was still open at this receipt; see 2026-08-09 refresh below.)
- Multi-project parent refuse now names itself: `incomplete_reason_class` /
  `error.code` = `workspace_root_refused` (#956).
- F10 MaxSim and DD-004 typed-boundary rows are **RETIRED** with dated receipts (#953) — not
  "fixed", not "still planned".

**What did not ship / still unavailable (Phase 2):**
- **edit-ready** (F5 Task 8 Steps 3–5), **verify-edit** (F6 Tasks 6–7 beyond Step 0), and
  **workspace** (F8 Tasks 12–13) remain **unavailable** for launch claims. They still need
  `rust_core/**` / `tests/e2e/**` work (cargo + e2e routing suite forbidden on the shared box →
  CI/cloud). Do not advertise them as ready product surfaces.
- CEO gates **#48 / #72 / #77 / #131 / #169** are untouched — recommendations only (short packets
  below); status stays `CEO_GATED`.

**PyPI / local (2026-08-06):** live PyPI was **v1.109.0**; Phase 0+1 code was on `main` awaiting
publish (installed wheel still pre-#956 at that moment). See 2026-08-09 refresh for published
wheel dogfood.

**Next (2026-08-06):** F7 Task 11 wave 3 = open PR #957 (C/C++). Broader READY→SHIPPED board flips
that lived in draft #960 were **not** absorbed here; #960 was **CLOSED** (superseded by this
Packet F reconcile #961) rather than undrafted.

### EXTERNAL / Phase 0+1 closeout refresh — 2026-08-09

Campaign packets A–F are complete on `main` and published. Hard stops held: no #169 spend, no
Task 2A / #89 / #90 GREEN claim, no silent CEO_GATED flips, no local shared-box `rust_core` cargo
for F5/F8 product builds, no Phase 2 edit/workspace launch claims.

| packet | PR | live disposition |
|---|---|---|
| A | [#951](https://github.com/oimiragieo/tensor-grep/pull/951) | MERGED |
| B wave 2 PHP | [#952](https://github.com/oimiragieo/tensor-grep/pull/952) | MERGED |
| B wave 2 C# | [#955](https://github.com/oimiragieo/tensor-grep/pull/955) | MERGED |
| C wave 3 C/C++ | [#957](https://github.com/oimiragieo/tensor-grep/pull/957) | MERGED — include-path engine + adapters |
| D refuse taxonomy | [#956](https://github.com/oimiragieo/tensor-grep/pull/956) | MERGED |
| E F10 + DD-004 | [#953](https://github.com/oimiragieo/tensor-grep/pull/953) | MERGED / RETIRED |
| F receipt + board | [#961](https://github.com/oimiragieo/tensor-grep/pull/961) + this refresh | claim matrix + CEO packets remain `CEO_GATED` |

**Published-wheel dogfood (2026-08-09):** `uvx --from tensor-grep==1.110.10 tg search needle C:\dev\projects --json`
→ exit **2**, `incomplete_reason_class=workspace_root_refused`, `error.code=workspace_root_refused`.
Live PyPI **1.110.10**. F7 Task 11 waves 1–3 are SHIPPED on the board (closure #963). Draft #966
(Task 2A) stays DRAFT / not GREEN.

#### Packet F — CEO recommendation packets (still CEO_GATED; do not implement)

Recommendations only. No silent reclassification. No question asked for the non-financial gates.

| id | topic | recommendation | status |
|---|---|---|---|
| **#48** | native front-door startup | Accept the shipped hybrid (native managed front door + Python sidecar). Retire a larger rewrite unless pip/uv parity is explicitly prioritized. | `CLOSED 2026-08-24` — GitHub issue #48 closed "not planned" applying this standing verdict; reopen if the rewrite is later authorized on its own merits |
| **#72** | public benchmark claim | HOLD public 7.5× (conflicts with later 6.4×; no committed current harness). Only a zero-spend fresh six-repo/180-task quality-gated re-run is in scope. **MEASURED 2026-08-23: the number is on NO public surface** — root `README.md`, the PyPI long description (`rust_core/README.md`, the file `pyproject.toml`'s `readme =` actually points at), `pyproject.toml`, and the GitHub About blurb all carry no headline multiple, and `include = ["LICENSE", "NOTICE"]` means `docs/` never ships. So there is nothing public to withdraw; the 7.5x/6.4x pair lives only in this board, `TASK_BOARD.md`, and one audit. The CEO gate is on PUBLISHING a number, not on retracting one. | `CEO_GATED` |
| **#77** / F9 | ledger enforcement scope | Local opt-in advisory only; no auth/CI blocking. | `CEO_GATED` |
| **#131** | GPU-flavor native assets | Optional experimental NVIDIA asset with CPU default/fallback and **no** speed claim. Physical proof/spend stays under **#169**. | `CEO_GATED` |

**#169** remains the only mandatory financial stop (physical GPU proof environment or spend).

### RECONCILED 2026-08-05 -- what actually shipped, per row

The list below this block still reads `READY` for rows that shipped today. Reconciling AT
completion, per the standing rule, rather than letting a stale READY invite a session to rebuild
finished work -- which is exactly how six queued items were found already-shipped this morning.

| row | true state | receipt |
|---|---|---|
| **#859** class-level atomic-writer census | **SHIPPED** | #937 widened the census 3 -> 41 modules; #945 classified all 16 violating identities; #946 closed the download TOCTOU; #947 retired the residual. Violating 16 -> 1, and that one is retired with a reopen condition. |
| **F7** language registry (Task 10) | **SHIPPED** | five waves: #927 Java, #928 C#, #930 PHP, #932 C, #934 C++. `_symbol_navigation_descriptor()` now reports 10 parser-backed / 0 foundational. Verified on the published wheels. |
| **F7** cross-file resolution (Task 11) | **SHIPPED** | waves 1–3 merged: Java #950, PHP #952, C# #955, C/C++ #957; board closure #963. (2026-08-05 row below was the sizing justification that preceded the build.) |
| **REF-CALL-REGISTRY** (Task 9) | **SHIPPED** | the dispatch ladders were removed as a side effect of the F7 campaign; `_references_and_calls_for_path` is four statements with zero language branching. Its missing Step 2 guard shipped in #940. NOTE: this row's description mislabels Task 9 as "prepare-service extraction" -- that is Task 6 Step 0. |
| **CPU-BACKEND** (Task 5) | **SHIPPED** | #925 (Rust `replace_in_place` discarded directory-mode failures and reported success) plus the CPU-backend TypeError retry that silently dropped `invert_match` and inverted results. |
| **F6** edit-verification (Tasks 6-7) | **Step 0 SHIPPED, rest multi-week** | #939 extracted `prepare_service.py` byte-identical. The remainder is ~10 versioned schemas, WSL path-domain extension, evidence signing and a 5 MiB bounded reader. |
| **F5** edit-ready (Task 8) | **Step 2 SHIPPED, rest BLOCKED** | #943 added `PrepareSnapshotV1` + `build_prepare_snapshot`. Steps 3-5 modify `rust_core/**` and `tests/e2e/**` -- cargo and the e2e routing suite are forbidden on this shared box, so they need CI or a cloud seat. |
| **F8** workspace (Tasks 12-13) | **BLOCKED** | modifies `rust_core/src/main.rs`, `path_domain.rs` and `tests/e2e/test_routing_parity.py`. Same constraint. |
| **MCP-SURFACE** (Task 4) | **BLOCKED on Task 2C** | Task 4 is titled "bump contract 1.8.0 -> 1.9.0"; the live value is **1.7.0** (`mcp_server.py`, `_TG_MCP_SERVER_CONTRACT_VERSION`). Task 2C performs 1.7.0 -> 1.8.0. Building Task 4 first bumps from a version that does not exist. |
| **#89 / #90** WSL path-domain | **BLOCKED** | owned by the Task 2B/2C typed-path program, which modifies `rust_core` and needs a real WSL host. |

**Start-now set after this reconcile: EMPTY.** Everything remaining needs CI/cloud, a CEO gate, or
demand evidence. That is a measured state, not a stall.

### Active / buildable

- **#89** — reproduced WSL-to-Windows path-domain defect; now `READY`, not environment-blocked. The
  bounded 2026-08-02 run proved a Linux `/mnt/c/...` directory exists while the delegated Windows
  native executable returned `path_not_found`. Owner: a new amended/re-reviewed TDD task; final
  closeout cannot pass until that task follows the implementation-PR/closure-PR lifecycle.
- **#90** — the doctor false-available half remains shipped in PR #571, but the WSL scan portability
  half is now reproducibly broken and `READY`: a raw `/mnt/c/...` file produced an unreadable-path
  warning plus false clear/zero matches, while the translated Windows-path control found six matches.
  Owner: the same amended typed-path program as #89, with scan-specific false-clear tests.
- ~~**#859** — class-level atomic-writer census/fix, Task 3.~~ **SHIPPED 2026-08-05, removed from
  this queue** — see the receipt row above (`#937` widened the census 3 -> 41 modules, `#945`
  classified all 16 violating identities, `#946` closed the download TOCTOU, `#947` retired the
  residual; all four MERGED). This row contradicted its own receipt row 24 lines above it for a full
  day. Left struck through rather than deleted so the contradiction is legible.
- ~~**F7** — cross-file resolution, Task 11.~~ **SHIPPED** — waves 1–3: Java #950, PHP #952, C#
  #955, C/C++ #957; closure #963.
- ~~**MCP-SURFACE** — Task 4.~~ **BLOCKED on Task 2C**, not buildable: Task 4 bumps the MCP contract
  `1.8.0 -> 1.9.0` and the live value is `1.7.0`. Building it first bumps from a version that does
  not exist.
- ~~**CPU-BACKEND** — Task 5.~~ **SHIPPED** (#925 plus the `invert_match` retry fix) — see receipt row.
- ~~**REF-CALL-REGISTRY** — Task 9.~~ **SHIPPED** — the dispatch ladders fell out of the F7 campaign;
  the Step 2 guard shipped in #940.
- ~~**F6** — Tasks 6–7.~~ **Step 0 SHIPPED (#939); the rest is multi-week**, not a ready row.
- ~~**F5** — Task 8.~~ **Step 2 SHIPPED (#943); Steps 3-5 BLOCKED** on `rust_core/**` + `tests/e2e/**`.
- ~~**F8** — Tasks 12–13.~~ **BLOCKED** on `rust_core/src/main.rs` + the e2e routing suite.
- **#89 / #90** — path-domain defect. **State disputed inside this file**; see the dependency-map row.
  A premise-checked build task was dispatched 2026-08-05 to settle it.

**CORRECTED 2026-08-05.** This list previously presented all ten rows as `READY`, while the
reconcile table 20 lines above recorded six of them SHIPPED or BLOCKED. Two rows — CPU-BACKEND and
REF-CALL-REGISTRY — were listed as buildable work with their own completion receipts in the same
document. A session trusting this list would have rebuilt finished code. Struck through rather than
deleted so the drift stays legible.

A row here is `READY` only if the reconcile table does not record it SHIPPED or BLOCKED. Its first
draft implementation PR moves it to `IN_FLIGHT` with the real PR number; only a separate post-merge
closure change may mark it `SHIPPED`.

### CEO-gated — exactly five

- **#48** native-front-door startup architecture. Recommendation only: accept shipped hybrid native
  managed front door + Python sidecar; retire larger rewrite unless pip/uv parity is prioritized.
  Status stays `CEO_GATED`; no question asked under the current instruction.
- **#72** public benchmark claim. Recommendation only: HOLD public 7.5x (conflicts with later 6.4x;
  no committed current harness); allow only a zero-spend fresh six-repo/180-task quality-gated
  benchmark. Status stays `CEO_GATED`. **MEASURED 2026-08-23: no public surface carries the number (root README, the PyPI long description rust_core/README.md, pyproject, the GitHub About blurb; docs/ never ships). Nothing to withdraw -- the gate is on PUBLISHING a number, not on retracting one.**
- **#77** / F9 ledger enforcement scope. Recommendation only: local opt-in advisory only; no auth/CI
  blocking. Status stays `CEO_GATED`.
- **#131** GPU-flavor native-asset publication. Recommendation only: optional experimental NVIDIA
  asset with CPU default/fallback and no speed claim; physical proof/spend stays under #169. Status
  stays `CEO_GATED`.
- **#169** physical GPU proof environment or spend — the only mandatory financial stop.

### Demand/research-gated — exactly six

- **#255** many-pattern dedup/compression/native investment selection.
- **DD-006** concurrent daemon load/DoS evidence.
- **AST-DSL-PARITY** full structural DSL/preprocessor-aware parity.
- **MCP-LEAN-DEFAULT** client demand and compatibility proof before a default flip.
- **CONTINUOUS-REFRESH** measured warm-session demand plus an approved search-index service design.
- **RUST-REPLACE-SYMLINK** public Rust direct-file leaf-symlink compatibility/security decision;
  reopen on a concrete untrusted-destination threat model or downstream compatibility decision.

### Terminal corrections from stale trackers

- **#22/F1** `RETIRED`: exit `0` complete match, exit `1` complete no-match, exit `2` incomplete;
  unhonoured GPU routing stays an in-band disclosure and does not independently change the code.
- **F2** `RETIRED`: legacy anonymous-agent compatibility deliberately retains the sentinel.
- **F10** `RETIRED` 2026-08-05: MaxSim late-rerank — unreachable via any `tg` install/command path
  and measured DROP on the golden set (ndcg@10 0.068 vs RRF 0.305); see dated census below.
- **DD-004** `RETIRED` 2026-08-05: standalone typed backend-error boundary — bank the AGENTS.md
  Backend Fail-Closed Contract; remaining `cpu_backend.py:811` `RuntimeError` is loud re-raise
  hygiene (INFO/WEAKENED), not a empty-success defect; see dated receipt below.
- **#109/#36/#37** `SHIPPED` in PR #605/#903/#908.

There are no environment-blocked canonical rows at this snapshot. The raw GitHub/CI/release and WSL
receipts are in `docs/audits/2026-08-02-backlog-reconciliation.md`.

> **Prior refresh 2026-07-29 (enterprise deep audit, live tip **v1.101.18**).** Spec:
> `docs/plans/2026-07-29-enterprise-deep-audit-design.md` (also mirrored under gitignored
> `docs/superpowers/specs/`). Headline corrections over the
> 2026-07-27b note below: (1) the text-disclosure **helper + leading banners are largely WIRED**
> (`_scan_truncation_warning` now covers `partial` + a fail-closed tail; `_emit_scan_incompleteness_banner`
> fires on map/context/context-render/edit-plan/agent/prepare/route-test/blast-radius-*); calling the
> campaign "not started" is stale — residual work is inventory **trailing** disclosure, codemap's
> ad-hoc `PARTIAL:` (only on `partial`, not every `_scan_incomplete` cause), mermaid's rendered
> visibility pin, and retiring the lie in `main.py`'s `_completeness_caveat_lines` docstring.
> (2) NEW/confirmed actionable items filed under Ready-to-build / LOW as **#858–#863** (local ledger
> IDs until the task store catches up). (3) MCP argv CWE-88 core builders still CLEAN; `mcp>=1.27.2,<2`
> caps the lockfile-canary class. Do **not** reopen GPU/cAST/free-threading settled battles.
>
> Prior refresh 2026-07-27b (post-**v1.101.4**) — **#276 IS CLOSED, AND THE CLASS BEHIND IT IS NOW
> THE CAMPAIGN.** The envelope wave below finished; an adversarially-verified census (4 read-only
> lenses, 22 agents, 14 findings confirmed / 3 refuted) then re-derived the whole surface and found
> the envelope was the *narrow* half of the problem.
> **What closed.** All ten tasks of the remaining-slices plan shipped (**#832** closes that plan as
> a campaign record, keeping its three RETIRED ideas — the `HashSet<PathBuf>` counter, the
> `incomplete_paths_count` rename, and the `SearchStats::is_empty()` "live bug" — with the evidence
> that killed each). **#834** retires the CEO-facing "tg is BEHIND on `--json`" verdict, which was
> false: measured on the shipped v1.101.4 against an ACL-denied directory (denial asserted to bite,
> readable sibling asserted still listable), `rg --json` exits 2 and `tg --json` exits 2 *carrying*
> `result_incomplete` / `incomplete_reason_class: "unreadable_path"` / `incomplete_paths_count` —
> exit-code parity, plus an in-band cause rg does not have. Scope is stated in the row itself: one
> shape, not a benchmark lead; #72 stays CEO-gated.
> **What the census opened.** DISCLOSURE POSITION was fixed for the symbol commands, `blast-radius`
> and `--mermaid` (**#822**, which also re-labelled a truncation wearing the advisory `note:` prefix
> and stopped it advising `--max-callers`/`--max-files` for a `--max-repo-files` cap — wrong-knob
> advice is the #762 failure). But position is the MILD half: on roughly a dozen surfaces the text
> path discloses **nothing at all**. `code-map`, `route-test`, `session open` and `agent` trail;
> `map`, `context`, `context-render`, `edit-plan`, `blast-radius-render` and `blast-radius-plan`
> exit `2` while saying nothing. Worst of the set and now fixed in **#831**: `tg scan`, a SECURITY
> ruleset, printed `Scan completed. total_matches=N` and exit `0` over files it could not open —
> dogfooded on v1.101.4, where `--json` reported `partial: true` with a 2-path `unreadable_paths`
> sample while stdout said nothing. Fixing it surfaced a second defect only dogfooding could find:
> the disclosure claimed `"2 file(s) could not be read"` for ONE blocked file, because two backends
> each attempt it and the counter counts EVENTS.
> **Contract hygiene from the same census.** **#830**: `CONTRACTS.md` still excluded the
> multi-pattern (task 317) and `gpu_native` (task 316) routes from the `incomplete_reason_class`
> allow-list months after both landed — and that exclusion was never actionable, since both
> envelopes stamp `routing_backend` from the same `decision.routing_backend()`, so the excluded
> route reports the identical `"NativeCpuBackend"` string as the included one. *An exclusion must be
> expressible in a field the consumer receives.* The same PR found two of three line-number anchors
> rotted onto unrelated code while the pinning test stayed green (it asserted the doc *contained*
> the strings, never that they pointed anywhere); anchors are now SYMBOLS and the test resolves each
> in the Rust. **#833**: `budget_remediable` had been on the MCP wire since #826 at contract `1.6.0`
> — `tg_repo_map` returns `build_repo_map(...)` VERBATIM, so a CLI-shaped edit in a file that never
> mentions MCP was a wire change. **A pass-through handler makes the producer it wraps an MCP wire
> surface.** Bumped to `1.7.0` with a ratchet pinning the declared wire key set.
> **NEXT CAMPAIGN (reframed 2026-07-29 — PARTIALLY SHIPPED, residuals remain):** the shared helper +
> leading-banner wiring largely landed after the 07-27 census prose was written. Finish the class
> with one ratchet enumerating every `_scan_incomplete`→Exit(2) site ↔ banner/delegate disclosure
> (**#861**), not a dozen one-off PRs. Still open residuals: inventory trailing notice position;
> codemap shared-banner parity; mermaid visible-node (not only `%%`) pin. The old "deadline arm
> silent because `_scan_truncation_warning` ignores `partial`" claim is **FIXED in code**
> (`main.py` deadline/partial branch + fail-closed tail) — update any skill/doc that still asserts it.
>
> Prior refresh 2026-07-27 (post-**v1.101.3**) — the **incompleteness-envelope** wave, closing
> the `--json`/`--ndjson` gap that the trustworthy-tg thread below had left as its load-bearing
> hole. The rule it settles: a machine consumer must be able to tell *truncated* from *absent*
> without reading stderr, and the exit code must agree with the envelope.
> **#276 closed across nine PRs**: **#795** gave `SearchStats` a walk-error count and **#808** fed
> it from the serial walk, so `--json` can no longer claim a complete scan; **#811** put the marker
> on the `main.rs` `SearchResultJson`/ndjson envelopes and **#818** made the multi-pattern route
> exit `2` when it discloses an incomplete walk; **#823** crossed the count to the `gpu_native`
> twin; **#793** gave the benchmarks three-state exit codes and one canonical marker set; **#821**
> stopped classifying an OUTPUT-write failure as an unreadable path; **#820** wrote down that
> `incomplete_paths_count` counts EVENTS, not distinct paths; and **#805** added
> `SearchStats::is_empty()` so the `Drop` guard cannot silently forget a new field — the class fix
> rather than the instance fix.
> What an EXTERNAL dogfood caught that our own gates did not: **#814** and **#816** killed two false
> claims (`not_found` asserted over a scan that read ZERO files, and a guard applied to a SHADOWED
> function while the real producer went untouched); **#819** found the same shape a third time in
> the truncation gates, which were blind to `--deadline` and so asserted absence over an unfinished
> scan; **#815** named the incompleteness fields the contract had never named; **#825** gave every
> CLI incompleteness cause a machine-branchable `budget_remediable` flag, so "would a bigger budget
> help?" stops being a guess.
> New surface: **#796** SARIF v2.1.0 for `tg scan`; **#806** exposed `incomplete_reason_class` over
> MCP; **#801** surfaced a live foreign ledger claim on `prepare`'s read-only path; **#800** bounded
> the session rebuild on BOTH refresh branches. Checkpoint honesty: **#785** reports a corrupt
> checkpoint as corrupt rather than missing; **#799** discloses the post-checkpoint edits `undo`
> discarded. **#804** fixed the trust benchmark, which had been measuring ripgrep twice and
> reporting it as tg.
> Test FORM, not just coverage: **#797** made output determinism a named CI-gated invariant;
> **#798/#809/#802/#817** retired the last flat wall-clock bounds, an incidental-byproduct
> assertion, and two latency assertions masquerading as hang guards. **#810** stopped the public
> docs citing local task IDs as though they were GitHub links; **#813** folded in the 8th oracle
> form and the drain-gate correction.
> LANDING WITH THIS RECONCILE (so this line does not go stale the moment it merges -- `gh pr list`
> stays the source of truth): the session-laws capture (**#824**), the `budget_remediable`
> extension to `repo_map`'s `scan_limit`/`output_limit` (**#826**, the only releasing one of the
> four), and three TorchBackend tests rewritten against a matcher that actually exists (**#827**).
>
> Prior refresh 2026-07-26 (post-**v1.98.27**) -- the **trustworthy-tg** wave (#292), continuing
> the unreadable-path honesty thread below across 14 releases. The through-line: a surface that
> cannot complete its work must SAY so, in a field a machine can branch on, and exit accordingly.
> What shipped: **#767/#768/#769/#770** carried the unreadable-subtree signal into `inventory`,
> `docs-coverage`, session snapshot and edit-plan validation discovery; **#764** stopped
> `session_store` reporting an UNREADABLE file as REMOVED (it was causing false-stale rebuilds);
> **#778** taught it to tell a dropped mount from a mass delete; **#773** made `docs-coverage`
> exit `2` on an unread path, since the old exit-`0` was defensible only while truncation could
> mean "raise the budget"; **#775** stopped `_path_is_relative_to` crashing on `OSError` and made
> the validation-plan family disclose dropped files; **#779** closed `codemap`'s post-walk silent
> drops; **#780** stopped `undo` destroying a file it could not capture for the revert; **#783**
> made `tg scan` name the files its rules could not read instead of reporting clean.
> Method, not features: **#771** pinned a Python silent-loss census and drained the three sites it
> caught in the previous week's own fixes; **#765** ratcheted the native walk-error-discard class
> so it cannot grow; **#776** pinned `scan_limit.truncation_cause` as a documented closed
> vocabulary; **#774/#772/#787** replaced tests that asserted a rendering, an unearned exit-0, or
> wall-clock overlap with ones that assert the contract. **#784** committed the trust benchmark
> harness AND reported what it actually shows — which was NOT a clean lead — and **#789** dropped
> its vanished-file column for scoring correct behaviour as dishonest.
>
> Prior refresh 2026-07-25b (post-**v1.98.13**, with v1.98.14 releasing) — the **unreadable-path
> honesty** wave. One question drove it: when tg cannot READ part of a tree, does it say so, or does
> it report success over a silently smaller result set? Answers shipped: **#757/#761** thread the
> existing `unreadable_paths` signal into `tg find`/`codemap`/incremental refresh and pin
> `incomplete_reason_class` in `CONTRACTS.md`; **#762** (v1.98.14) makes MCP `tg_search`'s
> `scan_limit` say WHY a scan truncated and whether raising the budget would even help —
> `budget_remediable: false` on an unreadable dir, contract 1.4.0→1.5.0, because the old payload gave
> WRONG-KNOB advice; **#763** stops an ACL-locked pytest basetemp from spamming every `git status`.
> Two defects found by tracing CONSUMERS rather than fixing the named site: **#286** (`session_store`
> reported a permission-denied file as DELETED — PR #764) and **#288** (`_capture_snapshot` drops
> unreadable files from the snapshot entirely, so they stop being tracked at all). **#765** ratchets
> the Rust-side `.filter_map(|e| e.ok())` walk-error-discard class (10 sites, mechanically censused —
> a prose note had said 6) so it cannot grow while #276's slices land.
>
> **Process receipts from this wave, all four costly:**
>
> (1) **PR #764 was NO-SHIPed THREE times by independent gates — every time for a false MECHANISM
> attached to a correct one-line fix, and every time by naming a consumer without checking it.**
> Draft 1 claimed repo-map eviction (`build_repo_map_incremental` ignores `removed`; the line cited
> as evidence says so verbatim). Draft 2 claimed a persisted list was re-served (it has no reader;
> health RECOMPUTES). Draft 3 named **`tg session health`**, which does not exist — there is no
> `@session_app.command("health")` and no `tg_session_health` MCP tool; `health` is only a request
> kind on the session serve/daemon protocol. **A disproved claim's replacement needs the same
> verification the original failed** — plausibility is not evidence. **GREP THE NAME FIRST.**
>
> (2) **Line numbers in prose about a file the PR EDITS rot by construction.** #764's citations went
> stale twice; the second time the very commit that fixed them shifted 10 of 12 by 16 lines. Fixing
> the instance a third time would have guaranteed a fourth, so the body now cites **SYMBOLS**, which
> do not drift. Line numbers are fine for files a change does not touch.
>
> (3) **`tag == PyPI` is NOT a sufficient drain gate.** A release-triggering commit sitting on main
> with CI still queued is a release IN FLIGHT even though the tag has not moved. And once the tag HAS
> moved, the publish may still be running — merging then can cancel it via the branch concurrency
> group and strand a tagged version with no artifact on PyPI. Check the release RUN's jobs.
>
> (4) **A subagent gate's result is EPHEMERAL** (#285, closed as an unrecoverable loss). Acting on a
> gate's blockers feels like discharging it, but silently drops everything it classified
> non-blocking — those items exist only in the returned text, not on the PR (`gh pr view --json
> reviews` on #757 shows only Codex boilerplate). Transcribe NON-BLOCKING items into a task or the PR
> body in the same turn the notification is read; the blockers self-record by being fixed.
>
> Prior refresh 2026-07-25 (post-**v1.98.11** — reconciling the v1.98.3 baseline forward across 8
> releases and 16 merged PRs. Headline: **the `--json` bug family is CLOSED** (#264/#266/#267/#269/
> #272/#273 were ONE defect — a renderer flag silently choosing the ENGINE and thus the FILE SET),
> with three ratchets so it stays closed: #752 the renderer/file-set invariant in both git and
> non-git topologies, #749 CI-actually-runs-the-native-e2e-suites, #745 an rg-grammar differential
> fuzzer. #279/#756 got cuda-gated tests EXECUTING (156/job) for the first time. The CEO `/goal`
> "beat rg cold-start" is closed as an honest NEGATIVE — tg's native walk IS rg's walk, same `ignore`
> crate, so widening relocates rather than accelerates; the value was BUGS, not milliseconds. Two
> non-defects retired on their merits: #270 downgraded to a guard, #277 closed outright. Full
> receipts in CURRENT STATE below. PR queue at reconcile time: #757 (draft, gated).
> Prior refresh 2026-07-24 (post-v1.98.3 — reconciling #735's v1.98.1 baseline forward four items:
> **#736** (C file-scope function-pointer VARIABLE, e.g. `void (*handler)(int);`, was mis-kinded
> `"function"`, now excluded — v1.98.2, two independent Opus gates; the banked one-line fix hypothesis
> "require `function_declarator` outermost" was WRONG, since a fn-ptr variable has it outermost too —
> the real tell is what that node's own `declarator` field WRAPS, a `parenthesized_declarator` around
> a `pointer_declarator` = variable vs. around a bare identifier = a redundant-paren REAL function
> `int (foo)(void);`) and **#737** (the C++ sibling of the same bug in `lang_cpp.py`, v1.98.3 —
> CLOSES THE C/C++ DECLARATOR BUG CLASS ON BOTH SIDES; C++-specific wrinkle: the member-fn-ptr
> `void (C::*mp)(int);` parses DIFFERENTLY BY SCOPE, at file/namespace scope its
> `parenthesized_declarator` wraps a `qualified_identifier`, in-class tree-sitter-cpp cannot resolve
> `C::` and emits an `ERROR` node beside a `pointer_declarator` instead — both excluded, but via two
> different guards; dogfood-verified on the published wheel) — plus two non-releasing PRs, **#738**
> (9 session lessons folded into AGENTS.md + 6 skills) and **#739** (replaced a twice-failed
> wall-clock-flaky test with a structural marker-order assertion,
> `test_create_checkpoint_lock_does_not_wrap_expensive_work`, 3 rounds/2 independent-gate rejections,
> CI-verified green on windows-latest py3.11 AND py3.12 — the exact platform that flaked); before that
> the top-10 symbol-graph language campaign, v1.93.10->v1.98.1: #723 validation-scan optimization,
> then 5 new languages — java (#725, v1.94.0) / php (#724, v1.95.0) / csharp (#726, v1.96.0) / c
> (#731, v1.97.0) / cpp (#732, v1.98.0, closing 10/10) — plus a go/php/csharp file-dependency
> foundational tier (#728, v1.96.1) and a coverage-honesty + payload-invariant fix (#733+#734,
> v1.98.1); prior: v1.93.2 — the CEO v1.92.1-dogfood "fix all + implement + dogfood" goal campaign (v1.93.0, #702-#706), executed end-to-end with a published-wheel 7/7 dogfood verdict, followed by the v1.93.1 (#708) banked-nit close-out and the v1.93.2 (#709) blast-radius scoring-prefilter fix + a session-capture skill/doc-library reconcile; before that v1.92.2 world-class-tier #249 + deep-research #251). **Live PyPI is v1.98.3 (2026-07-24). TOP-10 SYMBOL-GRAPH LANGUAGE CAMPAIGN COMPLETE — the top-10 language campaign (CEO-approved design plan, v1.93.10->v1.98.1) shipped 5 new languages this pass, java/c#/php/c/cpp, all FOUNDATIONAL tier (defs + imports; regex-fallback refs/callers) alongside the existing parser-backed py/js/ts/rust/go, closing the long-CEO-gated "next-language expansion" item (Ruby was not part of this wave). The symbol-graph tier split stays UNEVEN — java/c#/php/c/cpp are foundational (defs + imports, regex-fallback refs); python/js/ts/rust/go are parser-backed refs/callers — do not read "10/10 languages" as uniform depth. The two C/C++ function-pointer mis-kinding bugs disclosed during that campaign are now BOTH FIXED (#736 -> v1.98.2, #737 -> v1.98.3); true C/C++ `#include` resolution and true go/php/csharp import->file resolution remain DEFERRED to backlog (no manifest for C/C++; each of go/php/csharp needs its own project-config reader). Full per-release receipts in CURRENT STATE below. Fully published (verify `/simple`/`gh run list` before citing a version live if you are reading this soon after a fresh push — runner-scarcity can stretch a release to 30-60min queued, this is healthy not stuck). PR queue: EMPTY (0 open) before this reconcile PR opens.** The CEO `/goal`
> #232 campaign (2026-07-20) mapped the CEO's 9-point spec ("make tg REQUIRED vs rg/ast") one
> gap-point per release, one-per-publish, each independent-Opus-gated, all CPU-safe cloud+CI (never
> the shared desktop): **8 releases v1.84.0 -> v1.91.0, ZERO broken *published* releases, drain now
> CLEAR (0 open PRs).** **CEO#9 GPU-honesty:** `tg calibrate --json` now emits a structured
> `{"calibration_status": "skipped_no_cuda_build", ...}` line on a CPU-only build (a new
> `NoCudaBuildError` downcast in `crossover.rs`, exit code unchanged at 2) so a dogfood harness can't
> misread an honest CPU-only skip as a bare FAIL -> **#678 -> v1.84.0**. **CEO#1 never-empty
> best-effort-primary:** a `tg agent` scan truncated by `--deadline` before ranking ever resolved a
> primary target used to return an empty `{"file": "", "symbol": null}` -- now
> `_best_effort_primary_target_from_map` substitutes the best already-scanned symbol/file/most-central
> file, flagged non-authoritative via `partial_primary: true` + `primary_basis:
> "deadline_truncated_best_effort"`, with a STRUCTURAL `confidence.overall <= 0.55` cap (hardened by a
> gate nit from an emergent to a construction-guaranteed bound) so a partial result can never
> masquerade as confident -> **#679 -> v1.85.0**. **CEO#4 completeness you can trust:** a new
> bidirectional-oracle regression gate (`test_graph_completeness_oracle.py`) proves the documented
> three-state exit-code contract actually holds for `importers`/`callers`/`blast-radius` (exit 0 only
> on a truly complete scan, exit 2 -- never 0 -- on any cap/deadline cut), and closes a real parity
> gap it found along the way: `tg callers`' file-ordering only went likely-first above the 2000-file
> caller-scan ceiling, unlike `importers`, so a deadline cut on a smaller repo could strand a
> late-sorting caller -> **#680 -> v1.86.0**. **CEO#8 enterprise close-the-loop:** wires the existing
> signed `EvidenceReceipt` into a first-class CI gate -- `review-bundle create --receipt` embeds
> signed receipts, `review-bundle verify --against <PR-head-sha>` re-verifies each one's signature,
> trust, and revision-freshness against the real PR head (never `$GITHUB_SHA`, which resolves to a
> merge commit), and two new default-OFF policy levers (`--min-receipts N`, `--expect-key KEY_ID`)
> close a genuine empty-bundle bypass a post-gate NIT caught (a stripped-to-`[]` receipts list
> previously still verified `valid:true` because `all([])==True`) -> **#681 -> v1.87.0**. **CEO#5 `tg
> prepare`:** a one-shot edit-readiness CUJ (`tg prepare REPO "task"`) composes orient -> search ->
> agent -> route-test -> callers -> evidence -> ledger into one call -- primary target + confidence +
> `ask_user`, a callers/blast-radius floor, validation commands, and claim/evidence coordination
> hooks, all under the same `--deadline` exit-2 honesty contract as the rest of the agent-capsule
> family -> **#682 -> v1.88.0**. **CEO#6 AST parity that doesn't fight ast-grep:** `tg run`/`tg scan`
> zero-match exits now print a remediation idiom catalog instead of a silent empty result; the
> ruleset pack resolver accepts mental-model aliases (`auth`, `secrets`, `crypto`, `tls/ssl`,
> `subprocess`, `deserialize`) that resolve 1:1 to the matching canonical pack (never a guessed
> meta-pack); and a `$`-metavariable pattern with no usable ast-grep/native backend now raises a
> clean "Error: ..." + exit 2 instead of an uncaught traceback, and is NEVER silently rerouted to the
> native tree-sitter backend (different query DSL -- would return wrong/empty results) -> **#683 ->
> v1.89.0**. **CEO#2 mega-repo advisory auto-narrow:** a new `_detect_workspace_root` (reusing the
> same closed-vocabulary project-marker set `tg search`'s unbounded-root refusal already uses) stamps
> `workspace_root_detected: true` + a proactive `suggested_scope` on `tg orient`/`tg agent` when the
> target looks like a folder of several independently-cloned projects -- purely additive/advisory,
> the full unscoped result is always still returned, NEVER a silent re-scan or exit-code change ->
> **#684 -> v1.90.0**. **CEO#7 `tg install-dense`:** a one-shot `tg install-dense` installs the
> `semantic` extra (model2vec + numpy, torch-free) via the same `uv tool -> uv pip -> pip` cascade
> `tg upgrade` uses, then fetches the checksum-pinned dense model -- fail-closed on any
> pip/network/checksum failure, never a partial model; `tg find`'s BM25-only degrade message now
> points at it -> **#687**, bundled at release with **CEO#3's $0 doc-honesty fix** (README /
> `docs/installation.md` now say plainly that `pip`/`uvx` installs pay the ~150-250ms Python-
> interpreter floor (#48) and point stable-channel users at the native curl\|bash/PowerShell/npm
> front door for `rg`-parity cold search; `tg upgrade` already gets the native one) -> **#686**, plus
> a calibrate-stdout-JSON-only contract pin + daemon-deadline-route de-flake test nit -> **#685** --
> all three releasing together as **v1.91.0** (`#687`'s Rust command-enum collision with the
> same-day `#682` merge was keep-both-resolved at `bd3a142`, both `install-dense` and `prepare` enum
> variants + dispatch arms retained, re-verified CI-green across the full platform matrix, Opus-gated
> for the stale-venv trap + subprocess-safety + fail-closed model-fetch behavior). **Two headline
> fixes were BINARY-VERIFIED**, not just code-reviewed -- a clean-room `uvx --from
> tensor-grep@1.87.0 tg ...` dogfood confirmed both the GPU-calibrate structured skip on stdout and
> gap#2's truncated-agent emitting a real `primary_target` (never `null`). **CEO-gated, unchanged (out
> of AI scope -- do not build without an explicit CEO decision):** CEO#3's architectural half -- the
> native front door / public-shim startup-overhead reduction -- is **#48** (a currently-open GitHub
> issue; the ~30-40ms Python-interpreter floor caps how far shim tuning alone can close the gap);
> CEO#9's CUDA compute build is **#169** (>$100 spend); **#72** benchmark-numbers publish
> (public/irreversible); **#240-opt2** per-platform native wheels (a public-distribution decision).
> **#72/#169/#189-fork/#240-opt2 are this ledger's own task-store framing, not open GitHub issues** --
> re-verify with `gh issue list`/`gh issue view` before citing any of them as a tracked GitHub item.
> The prior CEO `/goal` "ultimate agentic toolkit" campaign (#224, CEO 2026-07-19, session Stop
> hook: "dogfood + build the ultimate agentic toolkit that saves on searches, uses contracts, supports
> agent-to-agent, [creative GPU], fix any regression, all tests green on the massive workspace incl LSP +
> symbol/codebase mapping, make AI smart without wasting tokens") shipped **8 PRs #668-#675 (v1.81.17 ->
> v1.83.0), one-per-publish, ZERO broken *published* releases** -- and it UN-GATED two long-CEO-held
> directions: **A2A (was #77/#99)** and **GPU ideation (was #169's spend gate on the compute build)**.
> **Headline: `tg ledger` -- the on-moat A2A code-coordination plane -- SHIPPED end-to-end,
> EXPERIMENTAL/default-inert.** `tg ledger claim/release/list` (advisory code-scoped locks, always exit-0 +
> an `overlaps` report, TTL-prune, crash-safe: a dead agent's claim ages out) = **#673 -> v1.82.0**; `tg
> ledger record/find` (content-addressed finding reuse -- the "saves on searches / uses contracts" pillar:
> revision-freshness stamps `fresh:false` on a dirty tree, integrity tamper-detect via a recomputed
> `receipt_digest` + `hmac.compare_digest`, refcount-safe blob GC) = **#675 -> v1.83.0**. Both compose ONLY
> existing primitives (`atomic_write_json`/`_index_lock` RMW, cross-process `index_lock`, evidence receipts,
> `_repo_revision_identity`) -- no new crypto/transport, no network/bus/task-queue, never a blocking lock --
> and each earned an INDEPENDENT adversarial Opus gate (path-confinement + cross-process concurrency for
> claims; integrity tamper-detect + revision-freshness for findings), then a **published-binary dogfood**
> (#225: agent-b sees agent-a's `overlaps` in production, exit-2 traversal; #227: record/find round-trips on
> the shipped wheel). **The deadline-SLA wave that preceded the ledger (#668-#672, v1.81.17-.21)** closed the
> last of the CEO-dogfood enterprise-scale gaps: **#669/v1.81.18** bounds the cold-`tg agent` post-deadline
> assembly tail (wall-to-partial ~= deadline + constant), but the v19 real-workspace dogfood then FALSIFIED
> its magnitude -- the dominant cost was a super-linear vendored-subtree dedup, fixed as the REAL win in
> **#671/v1.81.20** (an O(n^2) `resolve()` dedup = ~61% of `tg agent` wall, 90-144x faster) **[LESSON: a
> synthetic golden set does not carry MAGNITUDE -- the 3rd time this session a real-repo dogfood overturned a
> "fixed" claim; #222]**; **#670/v1.81.19** made `tg importers` scan deterministic-likely-first so a bounded
> partial still finds real importers; **#672/v1.81.21** gave `route-test` a default wall-clock deadline +
> partial-honest agreement under concurrent load; **#668/v1.81.17** shipped the LOW LSP follow-ups the prior
> reconcile had flagged as "queued not started" (exact `rustup component add` remediation + a `pygls>=2.0`
> floor). **#674/v1.82.1** (between the two ledger slices) bounds `tg codemap`'s git-identity calls + kills a
> `resolve()` storm so large workspaces degrade honestly -- and its Opus gate CAUGHT an incomplete
> `_excluded_by_output_str` signature migration (a `tg codemap --check` TypeError) that CI would have
> shipped. **Creative-GPU ideation** (the un-gated half of #169) produced 3 amortization-passing Tier-A ideas
> (GPU corpus-embedding index-build; reframe the already-built+correctness-proven native CUDA many-string
> engine as a `tg scan` many-rule whole-repo prefilter; GPU query-conditioned centrality) -- all build-gated
> behind #169's spend. **3 release transients self-healed via targeted `gh run rerun --failed`** (a crates.io
> `curl` flake, a session-daemon-start flake, a GitHub-API 503 outage on the v1.83.0 release-assets job #228)
> -- none a code regression. **Durable lessons banked:** build-agent commit-and-push disconnect (a slow
> full-suite run stranded a CORRECT #674 fix uncommitted while the PR head stayed broken -- verify the PR
> HEAD has the fix, not the agent's "fixed" claim); ruff-clean != mypy-clean (the Formatting&Linting gate
> runs BOTH `ruff` AND `mypy` -- #675 shipped a mypy-red nit fixed via a `TypeGuard[str]` predicate);
> launcher-shadow (a stale `~/bin/tg.exe` shadows the pip entrypoint -- `tg doctor` detects it, `tg
> repair-launcher` fixes it, dogfood via the explicit `Scripts/tg.exe`); PyPI wheel-lag (`info.version`
> flips before the abi3 wheels finish CDN propagation, so `tag==PyPI` per JSON != pip-installable yet).
> The prior senior-review + Rust-dogfood campaign (2026-07-17/18, CEO directive "review + fix
> + find dead/unused code + clean up", then a same-session Rust-repo dogfood) shipped 11 PRs -- **#655-#666**
> -- one-per-publish, ZERO broken releases, each independently Opus-gated pre-merge. **#655/v1.81.6** defers
> the fast-path-unused `directory_scanner` import in `bootstrap.py`: measured -24% (18.8ms off ~78.1ms)
> `import tensor_grep.cli.bootstrap` cost, but scoped ONLY to `--version`/`-V` and native `run`/`scan`/
> `test`/`ast-info` fast-dispatch (NOT `search`/`--help`, which still hit the broad-scan guard first) --
> explicitly a **partial** win on **#48** (public-shim cold-start): the bare issue-number parenthetical in
> the PR title triggered GitHub's own issue-linker despite the PR body's explicit answer that the fix was
> only a partial win, not a full resolution; the linker's action was manually reverted ~1hr later
> (18:16:37Z / 19:22:13Z) -- lesson: never put a bare issue-number parenthetical in a PR title/body unless
> the merge should actually terminate that tracked item. **#656/v1.81.7** adds one stderr line at `tg agent`'s two
> `typer.Exit(2)` sites distinguishing a trustworthy deadline-partial (high confidence, `ask.required:false`)
> from a genuine incomplete -- no JSON/exit-code change. **#657/v1.81.8** drops the inert
> `opentelemetry-sdk`/`-exporter-otlp` (zero configured `TracerProvider`, all 6 call sites already
> ImportError-guarded no-ops) and moves `pyarrow` into the `gpu` extra only (its 2 production consumers are
> both already gated behind `import cudf`) -- ~31-55 MiB lighter non-GPU installs; adds a governance test
> that now also checks the bare `[project.dependencies]` list (previously only extras were checked, which
> is how both drifted in unnoticed). **#658/v1.81.9 (audit C1/C2)** -- the prior campaign's deadline-honesty
> "COMPLETE" claim was FALSIFIED and re-fixed: `build_symbol_defs_from_map` was called bare (no
> `deadline_monotonic`) by 5 sibling `_from_map` builders + 2 cold wrappers, so its internal test-relevance
> scan ran unbounded on both the cold CLI and warm-daemon paths regardless of `--deadline`; live pre-fix
> repro `tg defs search --deadline 40` -> 113.5s exit 0 `partial:null` (a silent ~3x overrun of the 40s
> budget), impact/refs/callers/source similarly overran -- fixed by threading the deadline through all 7
> sites (mirrors the shipped #205 pattern) plus a return-time backstop on the cold `build_symbol_defs`
> wrapper. **LESSON: a "program COMPLETE" claim needs adversarial fresh-eyes on ALL stages + an OLD-vs-NEW
> real-binary repro, not a one-path dogfood.** **#659/v1.81.10 (audit C4, CWE-59)** -- `tg evidence emit
> --out` and `tg review-bundle create --output` (also the MCP `tg_review_bundle_create` tool) used bare
> `write_text` with no symlink refusal or atomicity; fixed via `session_store._write_json_atomic` extended
> with an `is_symlink()`-before-`.resolve()` guard. **#660/v1.81.11 (audit C3)** -- the MCP `tg_query`
> tool's `workspace_roots` fan-out had no cap and passed the FULL `deadline` to every root (20 roots x 60s
> = up to 1200s from one call); fixed with a fail-closed `_MAX_WORKSPACE_ROOTS = 8` cap (mirrors the
> existing `_MAX_INLINE_RULES=100` precedent) plus one shared monotonic deadline across the loop (not
> divided -- an early-finishing root gives back its unused time). **#661/v1.81.12 (audit B9/A18)** -- `tg
> edit-plan --max-files` visibly wired `max_edits` into `_suggested_edits_from_related_spans` but the
> callee never read it, so `suggested_edits` grew unbounded despite the flag; fixed via a new
> `_capped_suggested_edits` enforcement point (opt-in `suggested_edits_max`, default `None`/unbounded
> elsewhere). **#662 (swept into v1.81.13, non-releasing `chore:`)** -- dead-code cleanup: 255 LOC / 14
> symbols removed across `repo_map.py`/`main.py`/`agent_capsule.py`/`directory_scanner.py` (11 direct
> zero-reference removals + 3 cascaded orphans found while removing their sole caller); 10 of the task's 11
> seed candidates were FALSE POSITIVES on inspection (dispatch-table signatures, a stdlib callback
> contract, a Python protocol method, one already fixed by #661) and were deliberately kept --
> independent-Opus-gate proved every removal dead against the real tree. Flagged (not removed)
> `_negotiate_position_encoding` as an incomplete LSP feature needing a follow-up, which became #663.
> **#663/v1.81.13 (audit B13)** -- `_negotiate_position_encoding()` had zero call sites and no
> `@server.feature(INITIALIZE)` handler, so `ls._position_encoding` stayed permanently stuck at
> `"utf-16"`, giving wrong columns to utf-8/utf-32-negotiating LSP clients on non-ASCII lines; fixed to
> mirror pygls's own `ls.workspace.position_encoding` (verified against both `pygls==2.0.1`, the
> `uv.lock`-pinned version, and `2.1.1`) rather than re-deriving a second, potentially-disagreeing
> negotiation. Writing the behavioral test surfaced a SECOND, independent bug in the same file:
> `_to_cp_col`/`_from_cp_col` treated utf-8 as passthrough same as utf-32, which is wrong since utf-8 is
> variable-width -- added `_utf8_col_to_codepoint`/`_codepoint_col_to_utf8`. **#664/v1.81.14 (CEO dogfood
> find, tg 1.81.11/1.81.12)** -- `tg defs <FILE> <symbol> --provider lsp`/`hybrid` crashed with
> `NotADirectoryError`/WinError 267 because 10 call sites derived an LSP `workspace_root` straight from
> `repo_map["path"]` with no directory guard, reaching `subprocess.Popen(cwd=<file>)`; `--provider native`
> (the CLI default) never hit this path, so it shipped silently. Fixed via a new `_repo_map_root_dir()`
> helper (mirrors the existing `root if root.is_dir() else root.parent` pattern) threaded through all 10
> sites across defs/source/impact/refs/callers/blast-radius; directory-input behavior stays byte-identical.
> **#666/v1.81.15 (broader B9/#661 flag-lie, #212)** -- same flag-lie class in 3 more commands:
> `context-render` (full profile), `blast-radius-plan`, `blast-radius-render` all advertised `--max-files`
> but never bounded `suggested_edits` -- live-dogfood-verified on tensor-grep's own repo
> (`blast-radius-render --max-files 1` vs `50` -> byte-identical 73 `suggested_edits`/40 files, zero effect
> pre-fix); fixed the same way B9 did. **#665 (merged 2026-07-18, publishing as v1.81.16 -- C4/#659
> residual)** -- uniformizes the C4/#659 hardened-write pattern (precheck + same-dir-temp + fsync +
> `os.replace`; bare `O_NOFOLLOW` is a confirmed no-op on Windows) across every sibling atomic writer via a
> new shared `_index_lock.atomic_write_bytes`/`atomic_write_json` primitive: the biggest gap found was
> `checkpoint_store._write_json_atomic` having NO symlink precheck at all, and
> `audit_manifest._write_history_index` (the tamper-evident audit chain) being a fully bare `write_text`
> with zero hardening; also closed a 4th near-identical `dogfood._write_json_atomic` with a predictable
> (non-`uuid4`) temp filename. `codemap.py::_atomic_write_text` (doc-generation, a different risk class)
> explicitly left out of scope, flagged for a future pass. **The Rust-repo dogfood side-investigation
> (#210/#211/#214/#216) closed clean:** #214 (rust-analyzer-init reported as broken) is working-as-intended
> -- it is a missing-rustup-component ENV gap, not a `tg` bug; `tg`'s own doctor/detection code already
> references `rustup` at 4 sites (`scan_guardrails.py`/`main.py`/`lsp_provider_setup.py`/`bootstrap.py`),
> so no code change was needed. **VERIFIED CORRECTION to the prior "in flight" framing:** a
> `fix/lsp-polish-rustup-msg-pygls-floor-216` worktree was scaffolded for LOW-priority follow-ups (a
> friendlier rustup-component message, bumping the declared `pygls` floor from `>=1.3.0` toward the
> `uv.lock`-pinned `2.0.1`, a warm-daemon LSP parity test) but carries ZERO commits and zero uncommitted
> changes as of this reconcile (`git diff origin/main --stat` empty) -- queued, not started; do not
> describe it as "in flight." **CEO-gated, unchanged (verified via `gh issue list`/`gh issue view`, only
> #48 is a currently-open GitHub issue; #72/#169/#189-fork are this ledger's own task-store framing, not
> open GitHub issues):** #72 benchmark-publish (public/irreversible); #48's native-front-door
> architectural half (the ~30-40ms Python-interpreter startup floor visible at the top of every
> `-X importtime` trace, before `tensor_grep` is even reached, bounds how close a Python console-entry
> shim can get to `rg`'s ~7ms native start -- separate from this campaign's import-deferral win); #169 GPU
> enterprise (spend); #189-fork query-gated signal channels vs accept-the-find-ranking-ceiling (taste).
> **Prior campaign (2026-07-17):** the v1.79-v1.81.5 dogfood + deadline-honesty campaign is COMPLETE +
> drained clean, one-per-publish:
> the warm-daemon `--deadline` surface is now bounded end-to-end. **#200 (HIGH, dogfood-caught):** `tg agent
> --deadline` silently ignored on the default warm-daemon path -> #642 (cold residual) + #200-A/#647 (warm
> default deadline, v1.81.2) + #200-B/#648 (front-door anchor, v1.81.3), dogfood-VERIFIED on the published wheel
> (warm `tg agent --deadline 3` -> exit-2 + 'deadline' partial, 0/4 silent). **#203/#652 (v1.81.4):** bound the
> ~9 remaining warm cmds (context/defs/impact/refs/callers/file_importers/blast_radius family), independent-Opus
> -gated (all 9 non-vacuous, fail-closed holds). **#205/#653 (v1.81.5):** the refs internal-context-pack parity
> nit. **Two recurring release-flakes permanently killed:** #646/#202 (test_lifecycle TCP-connect) + #650/#204
> (test_index_lock_is_per_root_not_global wall-clock ratio -> overlap-invariant, validated on the flake runner).
> Also #201/#649 dogfood-harness false-negative, #643 MCP consolidation Phase-1, #198/#644 bench release-intent
> validator, #645/#199 context-render honesty; #189 CPU-moat research negatives recorded in `docs/PAPER.md` §3.10
> (#651, all three ColGrep levers dead/negative on REAL data -> lean-(c) accept-the-ceiling). ZERO broken
> *published* releases across the whole v1.79-v1.81.5 line. The prior `tg find`
> campaign #189 -- CPU semantic moat / ColGrep response -- shipped CLI (v1.77.0) + MCP tool (v1.78.0)
> this session on top of the v1.76.x "remaining AI-actionable backlog" wave #176, ZERO broken *published* releases).
> Shipped 15 PRs (v1.76.x wave): v1.76.0 #601 route-test / v1.76.1 #602 checkpoint-symlink / v1.76.2 #604 perf / v1.76.3 #603 daemon-guard /
> v1.76.4 #605 cuda-ceiling / v1.76.5 #606 orient-scope / v1.76.6 #608 agent-scope / v1.76.7 #610 daemon-coercion+rust-checkpoint-cleanup /
> v1.76.8 #611 checkpoint-symlink-disclosure (**security**) / v1.76.9 #612 GPU-calibrate-honesty / v1.76.10 #615 WSL-detection hardening (`/proc/version`) /
> v1.76.11 #617 device_detect-get_platform-WSL2-honesty / v1.76.12 #619 importers-directory-index-resolution (benchmark-found) /
> v1.76.13 #621 GPU-calibrate-honesty-nits (#612 gate NITs, #182); + #613 flaky-test-hardening + #616 help-contract-flake-fix (both no-release).
> Plus the `tg find` campaign #189, now fully MERGED and RELEASED (v1.78.1): v1.77.0 #626 CLI hybrid search (Wave 2b/2c) / v1.78.0 #627 MCP `tg_find` tool (Wave 2d) /
> #628 `TG_FIND_DENSE_WEIGHT` knob (Wave 3, chore, no-release) / #629 backlog reconcile (docs, no-release) / #632 `mcp` CVE-2026-52870 floor bump (fix, patch-released as v1.78.1);
> + #624 rank_chunks extraction (Wave 2a) + #625 T8 golden harness (Wave 1), both no-release. **On top of v1.78.1, still unreleased (chore, no-release):** #630 whitespace-gate the
> dense-weight classifier + nan/inf clamp (flip-prep, #191). **[v1.78.1-era snapshot; the CURRENT queue is EMPTY per the header above.] PR queue then: 1 open** (`#634`, `fix/find-dense-weight-flip` -- the `TG_FIND_DENSE_WEIGHT`
> default-flip itself, proposing to move the default from inert `1.0` to the swept 1:5 bm25:dense ratio for multi-word NL queries; per #191's evidence
> trail this is the still-open CEO checkpoint every skill referencing the knob describes as "not yet flipped" -- verify current PR state with `gh pr view 634`
> before citing either "flipped" or "still default-OFF" as current).
> Prior: v1.75.0->v1.75.4 GPU Phase-0 (#593/#594/#595/#596/#597, #173 reconcile); v1.73.0->v1.74.4
> (#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1; v1.69.0-.3; #142.

**Process:** deep-dive/audit (cite `file:line`) → verify-against-code → Sonnet TDD build in
`isolation:'worktree'` → real-venv verify (`uv run --active --no-sync`; copy `rust_core.pyd`, set
VIRTUAL_ENV+PYTHONPATH — a worktree "tests pass" is a hypothesis) → `ruff check` + `ruff format
--preview` + `mypy` (+ `cargo fmt --check`/`clippy` for Rust) → **mandatory adversarial Opus gate** if
it touches apply_policy/mcp/cpu_backend/index_lock/session_daemon/backends → PR → drain
(one-merge-per-publish). Match model to task. Common-sense gate before pending the CEO.

**Legend:** `P0` ship-blocking/#1 gap · `P1` HIGH bug/moat · `P2` MED · `P3` LOW. Status:
`[shipping]` open PR · `[ready]` buildable · `[wip-blocked]` cap-blocked (>5 PRs) · `[blocked]` gated · `[done]`.

**Drain discipline (hard-won 2026-07-10):** verify publish via `/simple` full wheel-pattern
`tensor.grep-1.58.N` OR the release run's publish-pypi=success — NOT a top-level "completed/success"
(can be a non-release run), NOT `grep | head` (head masks grep's exit). Stamp-on-main = Semantic
Release done (safe once /simple lists it). A run `in_progress` on "Python Semantic Release" = native
wheel compile (~65min normal), don't panic-rerun. **WIP CAP: no new build while >5 PRs undrained.**

---

## ⭐ EXTERNAL — enterprise closeout campaign session findings (2026-08-06)

Session closeout progress from the gotcontext-saddle / orchestrator seat. **Docs-only
receipt — does not authorize GREEN, releases, or STOP lifts.**

### START_NOW complete

- **#963** + **#964** MERGED; tip carries wave-2 plan `PROCEED_D1_THEN_W4`.
- Main CI for the #964 merge push completed success (docs merges; no publish lane).
- **PyPI still `1.110.0`** — these were docs merges only; product version unchanged.

### Task 2A W4 — in progress (NOT GREEN)

- Local branch `task2a-round60-red` (**NOT pushed**).
- Path: Sol R1 `FIX-FIRST` → repairs → Sol R2 `FIX-FIRST`.
- Cleared HIGH **#4** and **#6**; **6 HIGH remain**.
- **Do NOT claim GREEN.** No Sol exact-byte `SHIP`, no authorized Windows CI green phase.

### Explicit STOP unchanged

F5 / F6 / F8 / MCP / #169 / `CEO_GATED` — unchanged. Do not reopen or reclassify from this
receipt.

### Findings (session)

1. **First-pass HIGH1–10 repairs were vacuous vs Sol** — local “fixed” marks did not survive
   Sol re-audit; treat implementer-self-GREEN as non-evidence.
2. **Production-path oracles required** — scaffold / helper-only controls do not discriminate
   the defects Sol scores; oracles must exercise the real production path.
3. **Worktree `gitdir` WSL path breaks Windows git** — a worktree whose `.git` gitdir points
   at a WSL path is unusable from Windows `git`; create/manage Windows worktrees with
   Windows git only.

See also: `docs/audits/2026-08-06-enterprise-closeout-campaign-state.md`.

---

## ⭐ EXTERNAL DOGFOOD — v1.108.2 on gotcontext-saddle (2026-08-05)

Second external run on the same host, seven releases on from the 2026-08-02 one below. **Verdict:
CUJ stable across a big version jump (1.101.31 -> 1.108.2); the core agent contract is unchanged.**
Artifact: `/tmp/tg-dogfood-11082.json` on that host. Skills stamped 1.108.2.

Confirmed working: symbol ladder / blast (1.0 + mermaid) / imports / importers; orient / map / docs;
`agent` scoped + lexical + root `--deadline 90` (conf 0.9, root ~58s non-partial); truncation
hard-stop (conf 0.72 + `ask.required` + exit 2); `prepare --out/--claim` (~10s); `route-test`
agreement details; ledger Slice 1 + Slice 2; `evidence emit/verify` (`checks.digest_valid`); bare
`--json` search re-verifying `path_was_defaulted` + `scope_note`; GPU honesty; doctor autostart.

### Triage — what is NEW versus already-decided

| reported | disposition | receipt |
|---|---|---|
| **Parent-refuse class is generic `scan_limit`** — wants `workspace_root_refused` so agents do not confuse a refusal with file-cap truncation | **SHIPPED** — PR **#956** (merged 2026-08-06) | `_emit_broad_scan_refusal` gained `incomplete_reason_class`/`error_code` params; workspace guard emits `workspace_root_refused` for both. Dogfood on `C:\dev\projects` against main: exit 2 + class/code pair. Other ceilings keep `scan_limit`/`broad_scan_refused`. |
| **Caller-graph parity for Java/C#/C/C++/PHP** | **SHIPPED** — F7 Task 11 waves 1–3 | Java **#950**, PHP **#952**, C# **#955**, C/C++ **#957** merged. Sized by parsed C# re-measure in **#951**. |
| **Ship or forever-drop MaxSim** | **RETIRED** — F10 DROP receipt in **#953** | Caller/installability census + decisive negative on golden set; MaxSim reachable only via undocumented `TG_LATE_RERANK=1`. DD-004 RETIRED in the same PR. |
| Bare search exits 1, not 2 | **BY DESIGN — already retired as #22** | contract: exit 0 = complete with matches, exit 1 = complete with NO match, exit 2 = incomplete. The request (exit 2 + `missing_explicit_path`) is a CONTRACT CHANGE, not a bug fix — it would make "searched correctly, found nothing" indistinguishable from "could not search". Reopening needs an argument against that collapse. |
| Anonymous `--claim` still allowed (hint only) | **BY DESIGN — already retired as F2** | legacy anonymous-agent compatibility deliberately retains the sentinel; reopen only with a caller-supplied stable identity contract and migration plan. |
| GPU non-accelerative; `calibrate` exit 2 | **CEO-GATED (#169)** | the only mandatory financial stop; physical GPU proof environment or spend. |
| No fail-closed `edit-ready` / `verify-edit` / `workspace` | **BLOCKED, not missed** | F5 Steps 3-5 and F8 Tasks 12-13 modify `rust_core/**` and `tests/e2e/**`; cargo and the e2e routing suite are forbidden on this shared box. Needs CI or a cloud seat. |

**Two of seven "new features" were already built or already answered**, which is the value of running
the triage rather than queueing the report verbatim: caller-graph parity later closed via F7 Task 11
waves 1–3, and MaxSim has a measured negative sitting behind it. Queueing all seven would have
re-litigated settled work.

## ⭐ EXTERNAL DOGFOOD — v1.101.31 on gotcontext-saddle (2026-08-02)

A real user ran the published wheel against a live repo. **Verdict: works.** Symbol ladder, blast
(1.0 + mermaid), orient/map/docs, `agent` scoped+lexical+root (conf 0.9, ~55s non-partial),
truncation hard-stop (conf 0.72 + `ask.required` + exit 2), `prepare --out/--claim` (~8-13s),
`route-test` agreement, ledger Slice 1 + Slice 2 rollup, `evidence emit/verify` with
`checks.digest_valid`, `find` BM25 fallback, multi-project parent refuse, bare `--json` in-band
disclosure, GPU honesty, doctor autostart — all correct.

Below is every finding, **classified rather than queued**, because two of them collide with decisions
this repo made deliberately and one of those collisions is a false collision.

### F1 — bare search exits 1, not 2 (REPORTED AS A DEFECT; IT IS A CONTRACT QUESTION)

Reproduced on the published wheel:

```
tg search <no-match> --json          (no PATH)   -> exit 1, path_was_defaulted: true, scope_note present
tg search <no-match> f.txt           (explicit)  -> exit 1
```

The user's concern is exact: **an agent reading only the exit code cannot distinguish "searched the
right place, found nothing" from "searched a scope nobody chose, found nothing."** The in-band
disclosure IS there (#871 shipped `path_was_defaulted` + `scope_note` across all five JSON emitters),
so the gap is exit-code-only.

**But the requested fix — exit 2 + `incomplete_reason_class: missing_explicit_path` — contradicts the
0/1/2 contract.** Exit 2 means INCOMPLETE. A defaulted-scope search **ran to completion**; it just ran
somewhere the user did not choose. Making it exit 2 would drag the most ordinary invocation there is
into the incompleteness family and break the closed contract for every consumer.

**This is the SAME unresolved contract question as #22** (GPU exit-2 calibration: "exit 2 means
INCOMPLETE, and that search ran to completion and returned its match"). **Two independent findings now
point at one question**, which is what elevates it:

> *Does exit 2 mean "the scan did not finish", or "do not trust this result at face value"?*

Under the first reading both #22 and F1 are correctly WONTFIX and the answer is agent guidance.
Under the second, both become defects and a third code exists. **CEO/contract decision — not code.**
Options a decision should choose between: (a) keep exit 1, document that agents MUST branch on
`path_was_defaulted`; (b) add a distinct exit 3 for "completed but scope/result is not what you
asked for"; (c) widen exit 2's meaning and accept the blast radius.

### F2 — anonymous `--claim` — CLOSED as a non-issue, 2026-08-03 (the guard already shipped)

**RESOLVED. No design call is needed, and the "default-refuse vs default-allow" framing below was
answered by reading the code.** Three independent seats (an opus design council seat, a codex
`gpt-5.6-sol` xhigh seat, and a direct source read) each verified the same thing.

The original entry reasoned from this suppression condition:

```
new.agent_id != _DEFAULT_AGENT_ID and entry.agent_id == new.agent_id
```

and concluded two zero-config agents would drop each other's overlaps. **Read the first conjunct
again.** `_DEFAULT_AGENT_ID == "anonymous"`, so for an anonymous claimant `new.agent_id !=
_DEFAULT_AGENT_ID` is **False**, the suppression is **skipped**, and two anonymous agents **do** see
each other's overlaps. That guard *is* the #845 fix, and it is exactly what REFUSE was hoping to buy
— at zero UX cost. Pinned by `tests/unit/test_anonymous_claims_are_not_one_agent.py` (three arms,
including a premise test asserting the sentinel's literal value so a rename cannot silently un-fix
it).

**Why REFUSE is rejected on its own merits, not by inheriting the DERIVE retirement** (the entry was
right that a rejection is aimed at the form it names, so this is argued separately): the ledger is
explicitly **advisory** — a claim carrying overlaps still exits `0` and never blocks an edit — so a
wrong anonymous claim costs one coordination hint and nothing else. REFUSE would remove the
documented zero-config path and break `tg prepare --claim`, which has **no `--agent-id` flag of its
own**, i.e. it would ship a gate before its consumer could satisfy it.

**One REAL defect did fall out of this review, on the mirror path.** `release_claim` scoped `--symbol`
matching with `entry["agent_id"] == resolved_agent_id` — the same sentinel-as-identity confusion,
fixed on the CLAIM path and missed on the RELEASE path. Two anonymous agents both satisfied the
equality, so agent B's `release --symbol foo` silently released agent A's claim, contradicting that
function's own docstring guarantee. Fixed in **PR #914** with a measured red arm; an anonymous
`--symbol` release now matches nothing and returns a specific `unmatched_reason` naming `--claim-id`
and `TG_LEDGER_AGENT_ID`.

**Doc corrected alongside:** `docs/CONTRACTS.md` section 9 said `overlaps` lists claims "from OTHER
agent_ids", which is false for the deliberately-equal sentinel (PR #916).

### DEP-FLOOR-REACH — a `[tool.uv]` constraint is LOCK-ONLY (measured 2026-08-03, mostly NOT actionable)

**Recorded because the MECHANISM is durable even though 6 of the 7 instances are not actionable.**
Keeping this so the next advisory is handled correctly instead of re-derived under time pressure.

`[tool.uv].constraint-dependencies` governs **this repo's local resolution only**. It is not
published metadata, so it does nothing for `pip install tensor-grep[...]`. Published floors come from
`[project.dependencies]` / `[project.optional-dependencies]`.

Of 9 security floors, only `cryptography` was declared directly. The rest were lock-only:

| floor | reachable via | disposition |
|---|---|---|
| `aiohttp>=3.14.3` | `tritonclient[http]` ← `extra:nlp` | **FIXED, PR #911** — real: our lock held 3.14.1 and pip-audit failed on 3 live CVEs, and tritonclient permits `aiohttp>=3.8.1,<4` |
| `pyjwt`, `python-multipart`, `starlette`, `pydantic-settings` | `mcp` ← **core** | not actionable (see measurement) |
| `python-dotenv` | `pydantic-settings` ← `mcp` ← core | not actionable |
| `pygments` | `rich` ← **core** | not actionable |
| `requests>=2.33.0` | — | **vestigial**: absent from `uv.lock` entirely, constrains nothing. Drop it or wire it to something real. |

**Two decisive tests, both run — this is why the other six were NOT changed:**

1. *Can raising the direct parent's floor fix them?* **No.** `mcp` 2.0.0's own metadata permits
   `pyjwt[crypto]>=2.10.1`, `python-multipart>=0.0.9`, `starlette>=0.27`/`>=0.48.0` — all **below**
   our floors — and `pydantic-settings` is not a direct `mcp` dependency at all.
2. *What is the actual exposure?* **Zero, today.** A fresh resolve takes **latest**, and latest
   satisfies every floor: pyjwt 2.13.0, starlette 1.3.1, python-multipart 0.0.32, pydantic-settings
   2.14.2, python-dotenv 1.2.2, pygments 2.20.0, requests 2.34.2. Floors a fresh install would
   violate: **none**.

So declaring six more direct dependencies in published metadata would take real resolution risk — an
unsatisfiable floor can silently downgrade the whole install on a newer Python — for **zero measured
benefit**. Deliberately not done.

**If this recurs**, the mechanism is already in place: `scripts/validate_release_assets.py`'s
`required_extra_floors` mapping (added in #911) makes declaring a published floor a **data edit**,
not a new code path, and it ships with a red-arm test pinning the exact error text.

### F3-F4 — GPU non-accelerative / `calibrate` exit 2 · no `edit-ready`/`verify-edit`

GPU: already CEO-gated (#131/#169, deliberate HOLD). No change.
`edit-ready`/`verify-edit`: real gap, see F5/F6.

### Feature bets from the same run

| # | ask | disposition |
|---|---|---|
| F5 | `tg edit-ready` — one exit-0 ticket: prepare + non-anonymous claim + capsule/receipt + validation gates | **Strongest bet.** Composes shipped primitives; the fail-closed envelope is the moat this repo already claims. Depends on F2 (non-anonymous claim). |
| F6 | `tg verify-edit --capsule` — post-edit drift vs blast floor / touched files | **Strong.** Closes the loop `prepare` opens; nothing else verifies an edit against the floor it was planned from. |
| F7 | Caller-graph parity for C/C++ | **CLOSED (in-file scope) 2026-08-04 -- Task 10E shipped C++, the final wave.** `_symbol_navigation_descriptor()`: 10 registered / 10 parser-backed, foundational tier now EMPTY. Java (PR #927, Task 10A), C# (Task 10B), PHP (Task 10C), C (Task 10D), and C++ (Task 10E) all now ship in-file parser-backed refs/callers -- every registered language's `references_and_calls` is non-`None`. The REMAINING gap is narrower than this item originally scoped: cross-file caller confirmation for all ten languages still relies on the same text prefilter, so a `resolution_gaps` entry still names that reverse-import gap per language. C++'s confirmed band is deliberately narrower than PHP's/Java's/C#'s (bare-identifier + qualified calls + explicit `this->`, never an arbitrary receiver -- see `lang_cpp.py`'s TASK 10E docstring for the inheritance/`auto`/template reasoning). True cross-file forward+reverse resolution for any language is still open, tracked separately. |
| F8 | Workspace federated `prepare` across multi-repo parents | Open; parent-refuse works today, federation does not exist. |
| F9 | Ledger -> CI / review-bundle overlap gate | Open; composes `tg ledger` + `review-bundle`, both shipped. |
| F10 | Ship or delete MaxSim | **RETIRED 2026-08-05 — see the dated census + disposition below.** Unreachable by any `tg` command and decisively negative on the golden set; code left in place (deprecation note added), not advertised anywhere. |

### 2026-08-05 — F10 MaxSim: caller/installability census + RETIRE disposition

**Gate (from the row above): run a caller/installability census, then retire if unreachable.**
Nothing here re-runs the settled retrieval-quality experiment (`tensor-grep-maxsim-late-rerank-negative-2026-07-17`,
banked so it is never re-chased) or any benchmark/eval harness — this is a static code/doc census
only, per the shared-dev-box rule.

**1. Implementation + entry point.** `src/tensor_grep/core/retrieval_late.py` — pure MaxSim math
(`maxsim_scores`/`rank_by_maxsim`, :60-93), the `LateReranker` contract (:96-140), the real ONNX
encoder behind the `rerank` extra (`late_available`/`load_late_model`/`build_late_encoder`,
:171-356), and the checksum-pinned Hugging Face fetch (`fetch_late_model`, :430-484, plus the
`python -m tensor_grep.core.retrieval_late --fetch` CLI at :487-519).

**2. Callers — traced with an AST call-site count (`ast.Call`), not a substring/docstring grep,**
because this module's own docstring is full of the phrase "MaxSim" and would otherwise
self-confirm:
  - `src/tensor_grep/core/reranker.py::rank_chunks` (:296-355) is the ONE place `late_reranker.rerank(...)`
    is actually invoked (1 AST call site) — on a daemon thread joined against `TG_RERANK_BUDGET_MS`
    (default 2000ms), so a hung encoder cannot block the CLI.
  - `src/tensor_grep/cli/main.py` constructs it via `load_late_reranker()` at exactly 2 AST call
    sites: `_apply_semantic_rerank` (`tg search --semantic`, :4243-4261) and `find` (`tg find`,
    :4691-4708). Both are gated behind the SAME check, `os.environ.get("TG_LATE_RERANK") == "1"`
    (:4160, :4692) — there is no `--rerank`/`--maxsim` CLI flag anywhere in `main.py` (confirmed:
    `grep -c "TG_LATE_RERANK" src/tensor_grep/cli/main.py` → 4, all env-var reads/mentions;
    `grep -c "late.*rerank.*Option\|Option.*late.*rerank" src/tensor_grep/cli/main.py` → 0).
  - `src/tensor_grep/cli/mcp_server.py` has **zero** references to `late_reranker`/`LateReranker`/
    `TG_LATE_RERANK`/`rerank_hybrid`/`rank_chunks` (positive control: the same grep against
    `main.py` for `late_reranker` returns 10 hits, proving the pattern works — the MCP zero is a
    real absence, not a blocked instrument). The MCP `tg_find`/`tg_search` tools cannot reach
    MaxSim under any input.
  - Net: reachable **only** from two CLI code paths, both requiring an undocumented env var with
    no flag equivalent — never independently discoverable, and never reachable from the agent/MCP
    surface at all.

**3. Install path.** A real one exists in the strict pip-install sense: `pyproject.toml:634`
declares `rerank = ["tensor-grep[semantic]", "onnxruntime>=1.20", "tokenizers>=0.21"]`, and
`onnxruntime`/`tokenizers` are present and resolved in `uv.lock` (`grep -n "^name = \"onnxruntime\""
uv.lock` → :2710). But no `tg` command installs it or fetches the model: `tg install-dense`
(`main.py:15874`, `_run_install_dense` at :15805) installs only the `semantic` extra + the dense
embedding model — it never touches `rerank` or `retrieval_late.py`. The model itself must be
fetched by hand-running `python -m tensor_grep.core.retrieval_late --fetch` (no `tg` subcommand
wraps it), after which the user must ALSO know to set `TG_LATE_RERANK=1` with no discovery path
(not in `tg --help`, not a documented flag). This is the exact gap the external v1.108.2 dogfood
(2026-08-05, gotcontext-saddle) independently reported as "unreachable, no install path" —
confirmed correct in substance: a package-manager install path exists, a *product* install path
does not.

**4. Tests.** `tests/unit/test_retrieval_late.py` and `tests/unit/test_reranker.py` cover the pure
math, the `LateReranker` contract, and the wiring (env-off no-op, env-on reorder, budget-exceeded
degrade, malformed-shape degrade, hung-encoder non-block, `BackendExecutionError`/user-abort
propagation) against an INJECTED stub encoder — these DO run in CI (no real model needed).
`tests/unit/test_search_semantic_rerank.py` proves the CLI wiring end-to-end via `CliRunner`,
including `test_rerank_env_off_is_noop`, which spies on `late_available` and asserts it is NEVER
CALLED when the env var is unset (a genuine discriminating oracle, not a both-arms-pass check).
`TestRealFetchedModel` in `test_retrieval_late.py` (:410-449) is `@pytest.mark.skipif`-gated on
the real fetched model directory existing on disk (:409-415) — CI never fetches it, so those tests
are always skipped in CI; they exist for local dev only.

**5. Measured quality (already banked, not re-run here).** `docs/PAPER.md:469` records the
DECISIVE NEGATIVE, measured AFTER the role-aware query/document encoding fix landed (`retrieval_late.py`'s
`encode_query` param, #189 Item 1): rrf+maxsim ndcg@10 **0.068 vs plain rrf 0.305** on the 40-query
golden set (`CHANGELOG.md:20003`), and it actively HARMS the two lexical slices it was measured
against separately (`literal_golden.jsonl` / `identifier3_golden.jsonl`, delta -0.92/-0.97 vs bm25,
`CHANGELOG.md:20009-20010`). Root cause is diagnosed, not merely observed: raw MaxSim's mean rank of
the true answer is ~41 of 74 candidates, indistinguishable from the ~37.5 expected under a random
ordering (`CHANGELOG.md:20013`) — the 17M-param int8 `LateOn-Code-edge` model carries near-random
signal on in-repo code, a model-capacity ceiling the role-aware fix cannot address.

**Disposition: RETIRE.** Both halves of the gate close in the same direction — unreachable by any
`tg` command or documented path (§2-3) AND decisively negative even under the corrected encoding
(§5) — so this is not a "wire it in" gap, it is a validated dead end. Full code removal is **not**
done here: `reranker.py::rank_chunks`/`rerank_hybrid` thread the `late_reranker` param through the
SAME shared core the shipped BM25+dense RRF fusion depends on, `retrieval_late.py` is ~520 lines
with its own fetch/checksum/threading logic, and ~600 lines across three test files exercise it —
removal at that width crosses the risk bar this task sets for "leave a deprecation note instead."
The module docstring in `retrieval_late.py` now states RETIRED plainly (see the module-header edit
in this PR) so the next reader does not have to re-derive this census from call sites. Sweeping
`grep -rn -i maxsim README.md docs/harness_api.md AGENTS.md` plus the skill index found one live
over-claim this census would otherwise have missed: `docs/harness_api.md:1514` still described the
MCP `tg_find` tool as "(BM25 [+ dense [+ MaxSim]])" -- the exact advertised-but-unreachable pattern
already fixed in the CLI's `find --help` and the skill index (Battle 28 in
`tensor-grep-failure-archaeology`, #15 in `TASK_BOARD.md`), just missed there. Fixed in this PR
(`docs/harness_api.md`'s tool listing now reads "BM25 [+ local CPU dense embedding]" and notes
MaxSim is unreachable from the MCP surface). Every other MaxSim mention left in the repo now
either omits it or states the hold/retirement explicitly, so there is nothing else to purge.

**Reopen condition (both required, not either):** (a) a `tg` command provisions the `rerank` extra
+ fetches the model + flips the gate, so a user reaches it without reading source (e.g. folding it
into `tg install-dense` behind a real `--rerank`/`--maxsim` flag), AND (b) a re-run of
`benchmarks/eval_late_rerank_quality.py`'s T8 golden-set gate — on a DIFFERENT encoder, since this
one's ceiling is architectural, not the role-aware bug already fixed — clears the design doc's
original thresholds (`docs/plans/design-tensor-grep-late-rerank-2026-07-09.md`: nDCG@5 ≥ +0.03 abs
over RRF beyond 3-run noise, no recall@5 regression, p50 latency ≤ 2000ms). Absent both, this stays
RETIRED; do not re-flip `TG_LATE_RERANK` default or add a discovery path off a partial win on one
of the two conditions.

### 2026-08-05 — DD-004 typed backend-error boundary: RETIRE disposition

**Gate (from CEO audit / Task 14):** likely retire as a standalone row and bank the typed-boundary
rule as durable guidance — do not open a wrap campaign without a discriminating defect.

**1. Finding (banked, not re-audited).** Deep-dive DD-004 (`docs/audits/2026-07-31-tensor-grep-deep-dive.md`)
was INFO / wave2 WEAKENED: `cpu_backend.py` loud-re-raises a bare `RuntimeError` on search-loop
failure (`cpu_backend.py:811`) instead of `BackendExecutionError`. Evidence trail named
`cpu_backend.py:770-771` (now `:811` after intervening edits) and the search loop's catch site.
False-positive check already ruled out empty-success: the path raises; it does not return a clean
0-match `SearchResult`.

**2. What already holds the rule.** `AGENTS.md` "Backend Fail-Closed Contract" already requires
every `ComputeBackend` to raise `BackendExecutionError` on a real failure — never empty success,
never a silent engine swap. New/changed backend failure paths must follow that contract. The
standalone DD-004 row was a hygiene reminder sitting next to an already-documented law, not an
unowned gap.

**3. Why not wrap now.** The remaining bare `RuntimeError` is contract-hygiene only (severity INFO).
Wrapping it without a consumer that keys on exception type is churn: the CLI search loop already
treats non-`BackendExecutionError` exceptions as hard failures (`main.py` catch of
`BackendExecutionError` for CPU fallback, then a separate path for invalid-regex / re-raise). A
CPU-backend self-failure is not a native→CPU fallback candidate. No measured empty-success or
wrong-fallback receipt exists for this site.

**Disposition: RETIRE** the standalone row. Bank the rule in place (AGENTS.md Backend Fail-Closed
Contract — do not duplicate a second copy here). Leave `cpu_backend.py:811` as-is until a
discriminating reopen condition fires.

**Reopen condition (either):** (a) a backend failure path returns clean empty / 0-match success on
a real fault, or (b) a concrete consumer requires uniform `BackendExecutionError` typing at the
`cpu_backend` search-loop site (with a bidirectional oracle showing the bare `RuntimeError` breaks
that consumer). Absent either, do not reopen as a wrap-for-hygiene campaign.

### What the user fixed on their side

Skills stamped to 1.101.31; the stale "stderr-only PATH note" corrected to document
`path_was_defaulted`/`scope_note`; MaxSim removed from advertised `find` features; INDEX regenerated.
**Their correction confirms our #871 work landed and that the old skill text was the stale artifact.**

---

## ⭐ 2026-08-01 backlog campaign — shipped v1.101.28/29, and what the audits caught

**The board was stale for the 4th time: 9 of 24 open items were already fixed, refuted, or
deliberate-by-design.** Root-caused rather than corrected a 5th time — see
`docs/audits/2026-08-01-task-board-staleness.md` and the tolerance gate in
`tests/unit/test_task_board_freshness.py`.

**Shipped.** #883 (v1.101.28): a **CWE-88** hole in `apply_policy.py::_policy_file_arg` (a
repo-controlled filename beginning `-` was substituted into a `$file` template BEFORE argv
splitting, so it parsed as a FLAG); six prose lies claiming ledger Slice-2 does not canonicalize
PATH, all converted to DERIVATIONS rather than corrected lists; dead `sidecar.py::_classify_lines`.
#884 (v1.101.29): an invalid `--ltl` query escaping as a raw traceback at exit 1 instead of the
house `Error:`+exit-2 convention, plus `--ltl` missing from `SEARCH_PYTHON_PASSTHROUGH_FLAGS` — a
LIVE break for native-frontdoor users, not latent.

**NEW FINDING — a CI infrastructure gap nobody could have seen.** No job could test
native→Python-sidecar delegation end to end: `test-python` has the deps but never builds the release
binary; `native-build-smoke` builds the binary but installed only `pytest`. The entire delegation
surface was untestable, and the only possible symptom was a test nobody had written yet. Surfaced
only because the audit forced #884's e2e test into the job where a skip becomes a hard failure.
Fixed with a **pyproject-derived** dep install (a hand-list would rot exactly like the six prose
enumerations above).

**The audits earned their keep, and every real catch was in a VERIFIER, not the code:**

- The mandatory adversarial security gate — which the plan had **waived** and the audit
  **restored** on the rule that the trigger is the SURFACE, not the diff shape — found the CWE-88
  hole the plan never anticipated.
- Those CWE-88 tests then **could not distinguish a correct fix from a broken one**: two mutations
  (fixture-only prefixing; returning a wholly WRONG filename) both passed undetected. Rewritten to
  assert path IDENTITY over 9 shapes — the same mutation now kills 45 tests.
- A **Windows-only** call (`ctypes.windll`) reddened every POSIX leg — the dev-box-masks-CI trap.
  Guarded with `skipif` WITHOUT hollowing out the POSIX assertions (AST-verified single call site).
- One stated red arm **could not fail** (it skipped pre- and post-fix alike) and was withdrawn.
- The `--ltl` e2e test conflated ROUTING with EVALUATION, making it permanently red in a job that
  never builds the PyO3 extension. Measured against the published wheel first to confirm real users
  were unaffected, then split into a routing arm (always) and an evaluation arm (engine-gated).

**Method note worth keeping.** The 8-seat council and the codex pass overlapped on exactly ONE
finding out of nine. Six seats across five providers all missed the 15-test collision and the
unfalsifiable red arm — they converged on the most legible defect and stopped. Consensus is not
coverage; two structurally different audits were.

---

## ⭐ CURRENT STATE (2026-07-30) — authoritative; every section BELOW is HISTORICAL until the next full refresh

> **THE LIVE QUEUE IS `docs/TASK_BOARD.md`, NOT THIS FILE.** This document is 135KB of campaign
> history and is useful as an archive of WHY decisions were made. It is not a work queue and must
> not become a second one — reconcile against TASK_BOARD.md rather than adding a third list.

**Live PyPI: v1.101.27 (2026-08-01)** — derive, don't trust this line:
`python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/tensor-grep/json'))['info']['version'])"`.
(It read v1.101.19 until 2026-08-01, eight releases stale.) The 2026-07-28/29 wave shipped v1.101.12 → v1.101.19: the mcp
cap that unblocked publishing at all, both halves of a symlink hole (read-side disclosure and
write-side `--apply`), a machine-readable broad-scan refusal envelope, `imports`/`orient` false
zeros, both ledger coordination bugs, a CWE-88 argv sentinel, and the defaulted-scope search note
across all three dispatch routes.

### 2026-08-01 session — the skill library was documenting a repo it no longer matched (PR #882)

**The library's FACTS are sound; its POINTERS had rotted.** Eight of 28 skills audited, and every
one carried drift. Across them, all 14 flags, 11 env vars, every default value, and the 58-tool MCP
count re-derived exactly — but line anchors were adrift 14 to 500 lines, and one section dated
**2026-07-30 was already 250 lines stale two days later**.

Three findings were substantive, not cosmetic:

- **CRITICAL — `code-search-and-retrieval-reference` said "only C and C++ remain unregistered".**
  Both ship. `grep -c "lang_registry.register_language(" repo_map.py` → **10**, and
  `_symbol_navigation_descriptor()` → 5 parser-backed (go/js/python/rust/ts) + 5 foundational
  (c/cpp/csharp/java/php). A reader would have re-done shipped work, or scoped task #31 as
  "register C/C++" when it is "upgrade the five foundational languages to parser-backed".
- **HIGH — `tensor-grep-run-and-operate` §3 said `defs`/`source` do not take `--deadline`.** Both do
  (`tg defs --help | grep deadline`). §12's own table listed them as taking it, and the pitfall
  table warned against believing a stale "these don't take it" claim. **The document carried its own
  correction and its own error simultaneously**, 530 lines apart.
- **HIGH — AGENTS.md's never-re-stamp corrective pass said "all five of them" and listed FOUR.** The
  omitted seam, `_imports_with_lines_for_path`, was the one still stale — 392 lines, the largest of
  the five — sitting inside the very fix that introduced the law. A census and its own count are two
  artifacts; the count is not evidence about the census.

**Shipped:** `tests/unit/test_skill_library_drift.py` — pins every citation (must resolve to a
git-tracked file, line in range) and every stated `**N skills**` / `**Form N**` count against the
population it names. 7 mutation arms proven to fire. Plus `/tg-skill-audit`
(`.claude/workflows/tg-skill-audit.js`) for the semantic half, with its ledger DERIVED at run time —
a hardcoded fact is the exact defect it audits for.

**What the gate deliberately cannot do:** it proves a citation RESOLVES, not that the cited line
still contains the claimed symbol. That limitation is stated in the test's own docstring and in both
doc indices, because "there's a citation gate now" is precisely the claim that would stop the
reading which catches real drift.

**Historical status at the time, superseded by shipped receipts:** 20 of 28 skills were unaudited
(task #36); PR #903 completed the full 27-topic-skill audit. Task #37 recorded a deterministic local
grammar-dependent test failure; PR #908 added the explicit grammar requirement. Reopen either only
with a current failing receipt; do not relax assertions to match one box.

**Method note worth keeping:** building the gate produced three wrong readings before the truth
(100% broken → 60% → 100% ambiguous → 0 broken), and four separate greps returned false numbers
during verification, twice nearly rejecting a correct 38-anchor repair. Full receipts in AGENTS.md,
"Building ONE checker produced THREE wrong readings" and "GREP IS AN INSTRUMENT".

### 2026-07-31 session — one root cause behind two long-open items

**#868's "cause UNKNOWN" is CLOSED, and the reason it stayed unknown is the finding.** The PR sat
RED for days with two hypotheses recorded as "falsified by controls". One of those controls moved
the **wrong variable**: it tested that the `rust_core` Python **extension module** was present. The
dispatch gate is `resolve_native_tg_binary()`, which looks for the compiled **`tg` binary**. Two
different artifacts with adjacent names.

**CORRECTED SAME DAY -- the correction outranks the finding.** An earlier version of this entry
ended "and the killed hypothesis was right the whole time". That is NOT established. The two-arm
control proves the mechanism SUFFICIENT to produce CI's output; it does not prove that mechanism is
the one FIRING. `.github/workflows/ci.yml:688` says the job never builds the binary the control
forced. (`rust_core/Cargo.toml:58` declares `[[bin]] name = "tg"` in the same manifest maturin
builds, which cuts the other way -- also structural, also not a measurement.) Two structural
arguments pointing opposite ways is exactly when you stop arguing and measure;
`scripts/diagnose_gpu_delegation_route.py` does, with both controls. Writing "right the whole time"
repeated the error this entry is about, one level up.

```
main.py:7521   _warn_unavailable_gpu_device_ids(...)          <- the warning CI showed
main.py:7862   native_tg_binary = resolve_native_tg_binary()
main.py:7877   sys.exit(_delegate_to_native_tg_search(...))   <- EXITS HERE
main.py:8408   <#868's new exit-code rule>                    <- ~530 lines later, UNREACHABLE
```

Two arms, one variable, everything else byte-identical: `-> None` takes the Python route and exits
2 (why it passes locally, where cargo has never run); `-> Path(...)` takes delegation and exits 0
with **only** the inventory warning on stdout — reproducing CI byte-for-byte. A negative control
earns its authority by reproducing the failure in one arm; "still passes" rules out nothing.

Three consequences, all filed rather than noted:

1. **It is a PRODUCT bug, not a test bug.** Every real install HAS a native binary, so an
   unhonoured explicit `--gpu-device-ids` request exits 0 for actual users. Pinning the test to the
   Python route would be the flip-the-assertion-to-match-one-box anti-pattern.
2. **A test-harness defect wider than #868.** `_patch_cli_dependencies`
   (`tests/unit/test_cli_modes.py:373`) patches `Pipeline`, `DirectoryScanner` and
   `RipgrepBackend.is_available` — **not** `resolve_native_tg_binary`. Every test using it silently
   exercises a different dispatch route on a dev box than in CI. An unpatched dependency in a shared
   fixture is a hidden arm.
3. **Third instance of one class**, after #26's four dispatch routes: a rule implemented on the
   Python route while native delegation bypasses it. Being fixed with an enumerating mechanism, not
   another one-off. Law recorded in `AGENTS.md` ("A control that moves the WRONG variable falsely
   EXONERATES the right hypothesis").

**#26 (machine-first trust envelope) — Rust half shipped as PR #871.** The v1.101.22 dogfood
reported the same symptom for a FOURTH consecutive release ("PATH note is stderr-only; bare `--json`
returns empty aggregate JSON with no warnings/notes field"). The binary has to stamp it: `--json`
triggers native delegation and `_run_native_tg_search` STREAMS the document through
`_streaming_passthrough_returncode`, so Python never holds it — injecting the field there would mean
buffering the whole payload to fix a zero-match case. Enumerated rather than sampled -- and the enumeration was WRONG BY ONE on its first pass, which is
the more useful half of this entry. The census keyed on `#[derive(Serialize)]` and reported "4 of 4
covered": `NativeJsonOutput`, `SearchResultJson`, `SearchSummaryNdjson`, `GpuNativeSearchResultJson`.
A FIFTH emitter, `normalize_gpu_sidecar_json`, builds the same document by hand with
`serde_json::json!()` -- it shares no type with any sibling, so no derive-macro sweep can see it, and
it is NOT cuda-gated, so it is live in every build on the GPU-sidecar route. Caught by an independent
plan review, not by the census. **Enumerate EMITTERS, not the mechanism they happen to use.** All
five now carry `path_was_defaulted` + `scope_note`, held by
`tests/unit/test_scope_note_covers_every_json_emitter.py` (keyed on emitter, red-arm proven). Deliberately NOT
part of the incompleteness family — a defaulted-scope search RAN TO COMPLETION, so `result_incomplete`
would be false and would drag the exit to 2, breaking the closed 0/1/2 contract for the most ordinary
invocation there is. Gated on zero matches so the field stays worth reading. The note text moved to
the LIB crate (`native_search.rs::DEFAULTED_SCOPE_NOTE`) because `main.rs` is the binary crate — the
same constraint that moved `write_bytes_refuse_symlink` in #852 — and its trailing newline was
dropped, harmless while stderr was the only consumer but a real cross-engine divergence once the same
string became a JSON string VALUE. `tests/unit/test_scope_note_parity.py` reads BOTH sources and
compares; red arm proven by mutating the Rust constant.

**PER-SURFACE VERIFICATION, done before this refresh rather than assumed.** The previous NEXT
CAMPAIGN section named silent-Exit(2) surfaces as open. Checked individually against HEAD: `map`,
`context`, `agent`, `edit-plan`, `blast-radius`, `inventory`, `scan`, `codemap`, `route-test`,
`prepare`, `docs-coverage` all lead their text output with a disclosure, and `imports`/`orient`
were closed by #854. The class ratchet
`tests/unit/test_disclosure_covers_every_incompleteness_emitter.py` now holds the line. Deleting
those entries without checking would have been the already-shipped mistake in reverse — removing a
still-open item — which is why the check came first.

- **[HISTORICAL — v1.98.11 era] THE `--json` BUG FAMILY IS CLOSED — v1.98.4→v1.98.11, 16 PRs
  drained one-per-publish, zero rollbacks.** The campaign began as the CEO `/goal` "beat rg
  cold-start" and ended somewhere better: **the value was BUGS, not milliseconds.** A 3-seat council
  closed the speed lever for good — tg's native walk *is* ripgrep's walk (the same `ignore` crate,
  `Cargo.toml:44`), so widening it RELOCATES work and never accelerates it. Chasing it, however,
  surfaced one real defect wearing six faces.
  **THE ROOT GENERATOR:** the native-delegation gate keys on OUTPUT-FORMAT flags (`--json`/`--ndjson`
  sit beside `--cpu` in `bootstrap.py`), so a RENDERER silently picks the ENGINE and therefore the
  FILE SET. Every engine difference thus presents as an output-format bug. **#264** (JSON searched
  fewer files than plain), **#266** (`sinks::Lossy` corrupted CRLF/non-UTF-8 match bytes into U+FFFD),
  **#267** (`--no-ignore-vcs` dropped by the native walker), **#269** (the Python rg-passthrough
  sibling), **#272** (`--format`/`--lang` mis-parsed as valueless, so a filename could be eaten as a
  flag value), **#273** (the identical lossy emitter in `gpu_native.rs`) — one defect, all shipped.
  For an agent reading tg's JSON this was the difference between a complete answer and a quietly
  incomplete one: no error, no warning, just fewer results.
  **THE GUARDS that keep it closed** (a fix without a ratchet is a fix that comes back):
  **#752** pins `files(A) == files(A + renderer-flag)` across BOTH git and non-git topologies — the
  non-git arm is not redundant, `require_git(true)` means a different code path picks the file set,
  and testing one topology only is exactly what cost #750 a review round. **#749** asserts every
  native-binary-dependent e2e suite is actually RUN by CI (it was matching ONE hardcoded filename).
  **#745** replaced five rounds of "one more argv form nobody thought of" with a differential fuzzer
  against an independently-derived rg grammar model (70,040 cases, ~3s, release-blocking).
  **#279/#756** got cuda-gated tests EXECUTING for the first time — 156 lib tests now run per CI job
  where previously zero did, and before #754's `--all-targets` they were not even type-checked.
  **HONEST NEGATIVES, recorded so nobody re-spends the week:** #270 was downgraded from "root
  generator bug" to a regression *guard* after a published-wheel matrix showed the invariant already
  holding — the original "divergence" had been measured on a dev build on PATH that reports a real
  version string but accepts flags the published wheel rejects. **#277 closed as NOT A DEFECT**: the
  index engine never joined this family; it took the stricter total-refusal route in PR #541, two
  weeks before the field it was accused of dropping even existed. Use
  `uvx --from "tensor-grep==<v>" tg ...` for any claim about released behaviour.
  **Also in the span:** **#755** removed a 1-second-TTL race from the ledger forced-expiry tests
  (found by decoding a CI failure on a `.gitignore`-only PR rather than re-running it — the TTL was
  never the mechanism under test, so the timing dependency was deleted, not widened); **#753** fixed
  `.gitignore` entries that enumerated two observed paths instead of modelling the class, so tg's own
  state dir and `tg codemap` output had been polluting `git status`.

- **Non-releasing (2026-07-24). Test de-flake.** **#739** replaced a twice-failed wall-clock-timed
  assertion in `tests/unit/test_index_lock_concurrency.py` with a STRUCTURAL marker-order check
  (`test_create_checkpoint_lock_does_not_wrap_expensive_work` — asserts the checkpoint hot path
  never wraps the expensive work under the lock, via ordering markers, not elapsed time). Took 3
  rounds and 2 independent-gate rejections before landing clean. CI-verified green on
  windows-latest py3.11 AND py3.12 (run `30130861182`) — the exact platform/version combo that had
  been flaking.
- **Live PyPI: v1.98.3 (2026-07-24). C++ function-pointer variable fix — closes the C/C++
  declarator bug class on both sides.** **#737** ports #736/v1.98.2's C fix to `lang_cpp.py`'s
  duplicate declarator walker (`_cpp_declarator_name_node`): a file-scope or member function-pointer
  VARIABLE (`void (*handler)(int);`) was mis-kinded `"function"`; now excluded via the same
  parenthesized-declarator-wraps-what tell (`_cpp_parenthesized_declarator_wraps_bare_name`).
  **C++-specific finding, live-verified against real tree-sitter-cpp 0.23.4 parses:** the
  pointer-to-MEMBER-function shape `void (C::*mp)(int);` parses DIFFERENTLY BY SCOPE — at
  file/namespace scope its `parenthesized_declarator`'s single named child IS a
  `qualified_identifier` (excluded via the same bare-name type check as the plain C fix); IN-CLASS,
  tree-sitter-cpp cannot resolve the `C::` qualifier and instead emits an `ERROR` node alongside a
  `pointer_declarator` — TWO named children under the parenthesized wrap, excluded via a different,
  earlier code path (`len(named_children) != 1`) that never reaches the bare-name check at all. Both
  shapes end up excluded, but through two independently-verified guards, not one. A follow-up gate
  pass corrected the docstring to document both shapes (the first cut's regression-guard test used
  only the in-class fixture, which was already excluded pre-fix via the ERROR-node path regardless
  of the fix — not a real regression guard; split into a dedicated file-scope test that actually
  pins the repaired shape). Dogfood-verified on the published wheel. 12-shape TDD matrix added to
  `tests/unit/test_lang_cpp.py` (50 tests total, +1 from the gate follow-up); 175 across the lang
  sweep, 169 repo_map suite, all green.
- **Live PyPI: v1.98.2 (2026-07-24). C function-pointer variable fix — the banked known
  limitation from #731/v1.97.0, now closed.** **#736** fixes the file-scope C function-pointer
  VARIABLE mis-kinding (`void (*handler)(int);` was emitted as kind `"function"`) flagged as a
  known limitation when C landed (see the v1.97.0 entry below, now superseded). Two independent
  Opus gates. **The notable part: the BANKED one-line fix hypothesis was WRONG.** The original
  writeup guessed the fix was "require `function_declarator` outermost-direct" — but a
  live-verified real tree-sitter-c 0.24.2 AST dump shows a fn-ptr variable's declarator chain
  *also* has `function_declarator` outermost, same as a real prototype; that is not the
  distinguishing signal. The real tell, found only by re-deriving against the actual AST rather
  than trusting the banked guess: what the `function_declarator`'s own `declarator` field WRAPS —
  a `parenthesized_declarator` wrapping a `pointer_declarator` = a function-pointer VARIABLE
  (excluded); wrapping a bare identifier/type_identifier/field_identifier = a redundant-paren REAL
  function (`int (foo)(void);`, still included). Gate-1 returned SHIP but disclosed that the first
  cut then wrongly excluded that redundant-paren prototype shape; a same-PR refinement
  (`_c_parenthesized_declarator_wraps_bare_name`) fixed it without regressing the original bug fix.
  The `seen_function` boolean is never force-reset False->True, so a function that itself *returns*
  a function pointer (the `signal()` prototype shape, `void (*get_handler(int))(int);`) still
  resolves correctly via its own inner `function_declarator` hop. 35/35 `test_lang_c.py`, 163/163
  lang/registry sweep, 173/173 repo_map+skill_index_sync, 128/128 agent_capsule — zero regressions.
- **Live PyPI: v1.98.1 (2026-07-24). Coverage-honesty fix + the invariant repair it needed — the
  campaign's clean close-out.** **#733** fixed `coverage.language_scope` (the `tg agent`/`tg orient`
  capsule field advertising which languages the symbol graph covers): it was hardcoded to the
  pre-campaign `python-js-ts-rust` 4-language list, so a dogfood run on a Java/PHP/C#/Go repo
  under-reported real coverage; now derived DYNAMICALLY from `lang_registry` (the live 10-language
  two-tier scope), dogfood-found, not review-found. **#733's own larger descriptor then tripped a
  DIFFERENT governance test**: `test_importers_payload_is_far_smaller_than_map`'s <0.1x-map
  byte-ratio invariant — the lightweight `tg importers` envelope shares a `_envelope()` helper with
  the heavier `coverage` payload, so growing the coverage descriptor bloated importers' payload too
  (1076B vs a 974.5B floor, DETERMINISTIC not flaky); the build agent's own Windows self-gate ran a
  test subset that skipped `test_file_deps.py` and missed it. **#734** fixed it same-day by
  stripping the shared envelope keys symmetrically before the byte-ratio comparison, restoring the
  invariant without shrinking the coverage fix. **LESSON banked:** a dynamic descriptor that grows a
  SHARED response envelope can trip a payload-ratio governance test on the SMALL-payload side of
  that envelope; a self-gate's test subset is not the full CI matrix.
- **Live PyPI: v1.98.0 (2026-07-24). TOP-10 SYMBOL-GRAPH LANGUAGE CAMPAIGN COMPLETE — py·js·ts·
  java·c#·c++·c·go·rust·php, all 10 lit up for `tg orient`/`tg defs`/`tg source`/`tg imports`/`tg
  agent`.** **#732** ships C++ (`lang_cpp.py`, foundational tier, mirrors `lang_c.py`'s shape):
  functions (free/in-class/qualified out-of-class `Foo::bar()`/`Widget::~Widget()`/templated
  `Box<T>::get()`, all resolved to the bare name so a prototype and its out-of-class definition pair
  under one name), classes (`class`/`struct`/`union`/`enum`/`enum class`, forward declarations
  excluded), namespaces, type aliases (`typedef` + C++11 `using X = ...`), and `#include` (all 4 real
  `preproc_include` shapes). **Live-verified against two real, unmodified, fetched-fresh public
  headers, not just synthetic fixtures:** CPython's `object.h` (49 symbols clean, incl. an
  `#ifdef`/`#else`-guarded struct parsing both arms) and LLVM's `StringRef.h` — which surfaced and
  HONESTLY DISCLOSED the one real gap in the PR body: `class LLVM_GSL_POINTER StringRef {...}`
  misparses to kind `"function"` (the attribute macro becomes a fake return type) — an INHERENT
  ceiling of a preprocessor-unaware parser, indistinguishable from the legitimate
  `struct Point make_point() {...}` shape, so no guard was added (would suppress the legitimate case
  too); member recall mostly survives (StringRef's ~85 methods still resolve individually, only the
  enclosing class's own kind label is wrong). One related bug the same dogfood caught and DID fix: a
  macro-prefixed anonymous union in `object.h` mis-extracted the bare keyword `union` as a symbol
  name — fixed via a `_CPP_RESERVED_KEYWORDS` reject-list layered on the shared name-validity check.
  True `#include -> file` resolution stays deferred to BACKLOG (harder than go/php/csharp's own
  deferred resolvers — C/C++ has no standardized manifest at all). Also corrected a stale AGENTS.md
  claim ("8 of the top-10... C/C++ deferred") that #731 had left unedited.
- **Live PyPI: v1.97.0 (2026-07-24). C symbol + import intelligence (foundational tier) — Phase 1 of
  the campaign's last gap.** **#731** registers `"c"` in `lang_registry` (`lang_c.py`, mirrors
  `lang_go.py`/`lang_php.py`/`lang_csharp.py`): function definitions + prototypes (kind `"function"`,
  gated on the declarator chain passing through a `function_declarator`), struct/union/enum WITH a
  body (kind `"class"`; forward declarations excluded), typedefs (kind `"type"`, one record per
  declarator), and all 4 real `#include` node shapes (plain/quoted/macro-expanded/macro-combined)
  honest-unresolved. `.h` deliberately NOT claimed (already owned by the future C++ grammar per
  `_provider_language_for_path`'s pre-existing cpp assignment for every C/C++ header suffix).
  **Known limitation FIXED 2026-07-24 by #736 -> v1.98.2 (see the v1.98.2 entry above for the
  full mechanism; kept here as the historical record of the original finding).** As originally
  banked at this reconcile: a file-scope C function-pointer VARIABLE (e.g. `void (*cb)(int);`, no
  `typedef`) was mis-kinded `"function"` — `_c_declarator_name_node` (`lang_c.py:171-207`) set
  `seen_function=True` whenever a `function_declarator` node appeared ANYWHERE in the declarator
  chain, true both for a real prototype (`int add(int,int);`) and for a function-pointer-typed
  variable. **The fix hypothesis banked here at the time ("distinguish an outermost-direct
  `function_declarator` from one reached only through a wrapping
  `pointer_declarator`/`parenthesized_declarator`") turned out to be WRONG** — #736 found, via a
  live real-AST dump, that a fn-ptr variable's chain *is* outermost-direct too, same as a real
  function; the actual tell is what the `function_declarator`'s own `declarator` field wraps (see
  the v1.98.2 entry). Lesson: a banked one-line fix guess is a hypothesis, not a spec — re-derive
  against the real AST before coding it. Cross-file caller-graph stays deferred
  (`references_and_calls=None`,
  same foundational-tier contract as PHP/C#/Java/Go); `tg refs`/`callers`/`blast-radius` fall through
  to the generic regex-heuristic path (never a crash, never a fabricated AST hit), with an honest
  `resolution_gaps` entry.
- **Live PyPI: v1.96.1 (2026-07-24). `tg imports`/`tg importers` file-dependency FOUNDATIONAL tier
  for go/php/csharp.** **#728** (the investigation opened as a draft mid-campaign) extends the same
  honest-unresolved tier Java already landed (#725) to three more languages: new
  `go_imports_with_lines`/`php_imports_with_lines`/`csharp_imports_with_lines` extractors + shared
  membership in `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`/`_resolve_raw_import_entry`'s honest-unresolved
  branch — `tg imports` on a `.go`/`.php`/`.cs` file now returns real `{module, line}` rows instead
  of `result_incomplete`, every row `resolved=None, external=False` (never a fabricated path or a
  fabricated `external=True`). **Verify-first correction of the campaign's own scoping brief:** the
  investigation found java (already shipped, #725) was NOT actually a full-resolution reference as
  the original brief assumed — its own `_resolve_raw_import_entry` branch is foundational-tier only
  too, and `tg importers`'s reverse-confirm step (`_confirm_import_edges`) excludes java as well, via
  its own separate language allow-list (unchanged, still `javascript`/`typescript`/`rust`/`python`
  only) — so go/php/csharp were classified under one consistent TRUE-resolution bar: all three need
  resolver work go's `_go_import_path_to_dir` resolves to a PACKAGE DIRECTORY not a 1:1 file map;
  php/csharp have no `composer.json`/`.csproj`/namespace manifest at all. True forward resolution +
  the reverse-confirm allow-list both stay deferred to BACKLOG for all three (scoped precisely in the
  PR body for a follow-up). 12 new tests, 197 related tests green. **Also this pass (non-releasing
  docs):** **#730** refreshed the `tensor-grep-add-language` skill's worked example + seam
  line-number anchors after #728's insertions; **#729** triple-checked + refreshed the full
  27-skill library's citations against v1.95.0 (re-verified file:line anchors, session methods).
- **Live PyPI: v1.96.0 (2026-07-24). C# symbol + import intelligence (foundational tier).** **#726**
  registers `"csharp"` in `lang_registry` (`lang_csharp.py`): classes/interfaces/structs/records/
  enums -> kind `"class"`, methods/constructors -> kind `"function"` (an interface method signature
  and its class implementation both resolve as separate records sharing a name — no dedup, matching
  every real AST node being a legit hit); `using` directives -> imports. Lights up `tg
  orient`/`defs`/`source`/`imports`/`agent` for `.cs` files. Cross-file caller-graph deferred
  (`references_and_calls=None`), same foundational-tier contract as Go/PHP/Java. **Also this pass
  (non-releasing docs):** **#727** folded the session's learnings into `AGENTS.md`/`CLAUDE.md` (new
  *Adding a Language* + *Optimization Discipline* sections, the skill index, `.claude/skill_rules.json`)
  and registered the new `tensor-grep-add-language` skill documenting the 5 critical seams
  (most-forgotten: `_target_language_for_path`, the capsule confidence gate) — the handbook the rest
  of this language wave (C/C++) then followed.
- **Live PyPI: v1.95.0 (2026-07-24). PHP symbol + import intelligence (foundational tier).** **#724**
  registers `"php"` in `lang_registry` (`lang_php.py`, mirrors `lang_go.py`'s self-contained module
  shape, no import cycle with `repo_map.py`): classes/interfaces/traits/enums -> kind `"class"`,
  functions/methods -> kind `"function"`; `namespace_use_clause` imports recorded with PHP's `\`
  namespace separator preserved (an `as` alias not recorded, matching Python's own dotted-module
  convention). Lights up `tg orient`/`defs`/`source`/`agent` for `.php` files; cross-file caller-graph
  deferred, same foundational-tier contract.
- **Live PyPI: v1.94.0 (2026-07-24). Java symbol + import intelligence (foundational tier) — the
  top-10 language campaign's first new language.** **#725** registers `"java"` in `lang_registry`
  (inline in `repo_map.py`'s registration block, not a separate `lang_java.py` module): classes/
  interfaces/enums/records -> kind `"class"`, methods/constructors -> kind `"function"`; `import`
  declarations (plain/multi-segment/`static`/wildcard `.*`) -> imports. Wired at every dispatch site
  a real `.java` file needs (`_imports_and_symbols_for_path`, `build_symbol_source_from_map`,
  `_imports_with_lines_for_path`, `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`, `_resolve_raw_import_entry`,
  and the MOST-FORGOTTEN seam `_target_language_for_path` per the Go precedent's own code comment).
  Cross-file caller-graph explicitly deferred (`references_and_calls`/`provider_alias_calls`/
  `file_imports_symbol_from_definition`/`import_update_target`/`prime_repo_context` all `None`) —
  degrades HONESTLY: `tg callers`/`tg blast-radius` on a Java target return empty (never a crash,
  never a fabricated hit) plus a labeled `resolution_gaps` entry.
- **Live PyPI: v1.93.10 (2026-07-23). Post-#719 profiling probe's SECOND (previously-deferred) lever,
  now shipped.** **#723** adds a byte-identical textual pre-check to `_framework_test_pattern_bonus`
  (`repo_map.py`) before its per-candidate AST parse (`_framework_test_function_candidates` ->
  `_python_parametrized_test_function_candidates` -> `_cached_ast_parse`) — the #719/v1.93.9 reconcile
  had profiled this at 46% of `tg context-render`'s wall / 23.9% of `tg prepare`'s and DEFERRED it as
  "no clean path"; a fresh profiling pass found the byte-identical substring pre-check that had been
  missed. **Microbench on the shipped wheel: 3657ms -> 1172ms across the target function (~68%
  faster).** Byte-identity holds via a WORD-SPLIT pre-check (not a naive whole-term contiguous check)
  — verified against a constructed adversarial counter-example where a JS `describe`/`it` synthesized
  suite-join candidate straddles the artificial join space; the naive check would have wrongly
  short-circuited it to a false 0, the shipped word-split check correctly proceeds to real scoring. 5
  new tests incl. a call-counting monkeypatch proving the AST parse is actually skipped, not
  coincidentally equal; 69/69 `test_validation_commands.py` + a broader ~200-test regression sweep
  green. **Lever 2** (threading `precomputed_file_paths` through the second validation-plan chain)
  verified ALREADY shipped in #645 — skipped, not bundled, nothing left to build.

- **PR #728 (opened as a draft mid-campaign, referenced here when this section was last touched
  mid-flight) shipped as v1.96.1 — full receipt in the v1.96.1 entry above; this stub line is kept
  only so the historical draft-state framing below it doesn't read as still-current.**
- **Live PyPI: v1.93.9 (2026-07-23). Post-campaign optimization pass — a fresh `cProfile` probe of the published v1.93.8 hot paths (orient/callers/imports/agent/prepare) found 2 levers; the clean one SHIPPED and is DOGFOOD-VERIFIED ~54% faster on its target function.** **#719/v1.93.9** merges the 3 redundant full-tree `ast.walk()` passes in `_python_imports_and_symbols` (repo_map.py) into ONE — measured **82% of `tg orient`'s cold wall** (also ~53-67% of callers/agent). BYTE-IDENTICAL by construction (Import/ImportFrom/ClassDef/FunctionDef/AsyncFunctionDef/Call are mutually-exclusive node types; the trailing `sorted(dict.fromkeys)`/`symbols.sort` make interleaved append-order irrelevant); INDEPENDENT-OPUS-GATED **SHIP** via a 386-file OLD-vs-NEW differential (4960 imports + 10220 symbols compared, **0 mismatches**); the build also removed the now-orphaned `_python_dynamic_import_entries` (its last live caller went away -- #716 removed the other). **DOGFOOD-VERIFIED on the published wheels:** a microbench isolating the function (ast-parse lru-cached, so it times only the walk-merge) = v1.93.8 961ms -> v1.93.9 446ms across 80 files = **~54% faster (>2x)**. The 2nd probe lever (framework-test AST scan in `_discover_validation_tests_for_primary_file`, 23.9% of prepare) was measure-first **DEFERRED** -- no clean path (gate-it = validation-test recall regression risk; parallelize = GIL-uncertain + `@_mtime_aware_cache` thread-safety). Walk-merge lever class now EXHAUSTED (all `ast.walk` sites in repo_map.py swept; the hot redundant-walk fns were #716 `_python_imports_with_lines` + #719). Also this pass: **#720** (test-only, NON-releasing) de-flaked the 2 uncontended hot-path perf-floor asserts in `test_index_lock_concurrency.py` (the #244 ratio form itself flaked at elapsed=4.531s vs the flat 4.0s floor on a loaded runner -- root cause #244 missed: `baseline_elapsed` omits the snapshot-WRITE I/O that `elapsed` pays; widened to `max(baseline*6, 8.0)`, bidirectional guard preserved; the 2 stale-lock-reclaim asserts KEEP flat `<4.0` -- there 4.0 is SEMANTIC, must beat the 5s acquire timeout). LESSON: a WARM `tg orient` dogfood measured the CACHED repo-map path (the function never runs) -> a false -36% artifact; verify a COLD-path optimization by microbenching the function (parse-cached) or clearing `.tensor-grep` between reps, NOT a warm end-to-end run. Both #719/#720 worktrees pruned; drain clear. Tools: scratchpad/opt10/{microbench_astwalk,dogfood_v1939_orient}.py.

- **Live PyPI: v1.93.8 (2026-07-23). The CEO `/goal` "deep-dive + optimize until a +10% overall increase in speed AND output AND accuracy, dogfood-verified" campaign — ACHIEVED at +25.3% overall on the published v1.93.8 wheel (2.5x the 10% target).** A scorecard + baseline were FROZEN before any work (scratchpad `scorecard_definition.md` + `baseline_results.json`, oracle-validated, commit a002d7f1); the goal is the frozen sec-4 composite `overall = mean(speed_leg, accuracy_leg, output_leg) >= 0.10`, 3 legs equally weighted. **RESULT (uvx published-wheel, clean env): speed_leg +13.4%** (median of 8 cold cells: S5 `prepare` +29.8% [map-reuse #714 + O(k) source-truncation #713], S6 `imports` +17.7% [walk-merge+stdlib-fastpath #716], S4 `callers` +15.0%) **· accuracy_leg +62.6%** (the scorecard's `rrf` arm ndcg@10 0.3047->0.4953 via **max-combine fusion #717**: best-rank-wins `max(1/(k+rank))` per leg vs the old `sum`, so the near-floor bm25 leg can no longer DRAG strong dense results down) **· output_leg +0** (results-identical). **Capsule 16/16 agent-accuracy HARD floor HELD; no regression on any class.** SHIPPED v1.93.3->v1.93.8 one-per-publish, ZERO broken *published* releases: the speed wave **#711-#716** (warm-deadline thread, O(k) truncation, prepare map-reuse, imports fast-path, cold-start import-deferral, accuracy-regression harness) then the accuracy lever **#717** (max-fusion default flip). DISCIPLINES that delivered it: **measure-before-build** overturned a cProfile-inflation pessimism (a lens predicted ~4%; the real combined wall was +13.4%); an experiment (`fusion_experiment.py`) VALIDATED the fusion lever on the frozen golden set BEFORE the load-bearing default flip; the **independent Opus gate caught a real single-token-literal regression** the build agent's NL-only dogfood missed (max -0.0369 ndcg on `literal_golden.jsonl`) -> folded a conservative fix (`_find_combine_mode` routes single-whitespace-token queries back to `combine="sum"`, NL keeps max; `reciprocal_rank_fusion`'s default stays max so the scorecard arm is preserved); the frozen scorecard was **NEVER goalpost-moved** (a goal-interpretation fork was surfaced to the CEO, who chose the strict 3-leg reading). HONEST framing: this is an accuracy-LED +25% (the frozen baseline's fusion was genuinely underperforming -- rrf 0.30 vs dense-alone 0.60 -- so the fix is a big relative gain), speed real-but-smaller, output flat. The output levers (O1 bytes/O2 completeness) and the incomplete #3 (`_context_tests` double-pay) are UNNEEDED -- accuracy alone cleared the target. Tools: scratchpad/opt10/{remeasure_speed,compute_speed_leg,compute_composite,fusion_experiment}.py.

- **Live PyPI: v1.93.2 (2026-07-22, #709).** Closes the first of the four follow-ups banked in the
  v1.93.0 entry below: the blast-radius reverse SCORING prefilter now excludes `dynamic_unresolved`
  literals (the #703 dynamic-import honesty fix), so `affected_files`/`dependent_files` no longer
  fuzzy-pulls a same-named decoy module into a blast-radius result. Landed behind a **pin-first
  ranking gate** (a test pinning the CURRENT ranked output GREEN on base *before* the change, so any
  legitimate-entry reorder after it is a STOP-finding, not noise) -- `test_blast_radius_legitimate_dependent_ranking_pin`
  proved zero legitimate reorder. The fourth banked follow-up (the in-repo `tensor-grep-ledger` skill
  question) is resolved by this same session-capture reconcile: all 6 CEO-drafted skill folders
  (ledger/prepare/gpu/find-and-route/multi-project-search/enterprise-review-bundle) are now registered
  in both `AGENTS.md` and `CLAUDE.md`'s skill indexes, `test_skill_index_sync.py`-green. **CEO desk
  (unchanged):** #72/#169/#255/#189-fork/#240-opt2.
- **Live PyPI: v1.93.1 (2026-07-22, #708).** Closes the middle two of those same four banked
  follow-ups: the bootstrap oversized-implicit-root probe now forwards each `--no-ignore*` flag to
  its own field (parity with the sibling large-root guard, which already had per-flag fields); a new
  structural bounded-probe cost-pin test proves the walk stops at `ceiling+1` rather than completing
  a full unbounded walk on a huge implicit root; `_agent_gpu_tg_command` now pre-resolves a bare
  `"tg"` via `shutil.which` before the WSL cross-domain gate runs, so an absolute path always feeds
  that gate; plus one stale citation fix. **CEO desk (unchanged):** #72/#169/#255/#189-fork/#240-opt2.
- **Live PyPI: v1.93.0 (2026-07-22). The CEO v1.92.1-dogfood GOAL CAMPAIGN ("fix all of those issues + implement all of the needs-improvements, then dogfood it") executed END-TO-END in one session: 6 items -> 6 agents -> 5 Opus-gated PRs + 1 evidence-adjudicated HOLD -> 2 releases -> a published-wheel closing dogfood, 7/7 PASS.** Ships: **#702/v1.92.3** unscoped-search fast-refuse (the DEFAULT flag-less/pip-only `_run_rg_passthrough` path had NO walk ceiling -- natively reproduced, not a WSL artifact; bounded probe on `paths_defaulted` only, `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` single-sourced across all 3 doors; gate: ZERO false-refused shapes) -> then the **documented rapid-window BATCH** (the v1.91.0 precedent) merged 4 PRs into ONE combined release **v1.93.0**: **#703** dynamic-import false-edge fixes (the asked-for feature was ALREADY SHIPPED (#504); execution-verify found relative `import_module(package=...)`/`__import__ level=1` resolving to DECOY top-level files -> honest `dynamic_unresolved`, decoys excluded both directions) · **#704** WSL GPU-probe fix (installer's bare-named POSIX shim wraps tg.exe; suffix-only cross-domain detection misclassified it -> untranslated /tmp path = the reported `path_not_found`; dual-signal detection, live-verified on the reporting box; + gate-folded fail-closed/bounded metadata read) · **#705** UX/honesty batch (ALL dense hints lead with `tg install-dense`; doctor cold-daemon `autostart: on-first-use` field; anonymous prepare-claim `agent_id_hint`; **`tg prepare --out FILE`** byte-identical capsule persist -> `evidence emit` chains without a manual save; + a found-fix: `--semantic` was missing `tg find`'s friendly degrade hint) · **#706** ledger PATH-footgun (ROOT CAUSE was physical: each cmd resolved the STORE dir from the literal PATH -- `claim core/hooks` + `list .` used two different stores; fix = nearest-`.git` canonical store (worktree `.git`-FILE correct) + stored `scope` + subtree rollup + release honesty + a gate-folded CONTRACTS migration note). **CLOSING DOGFOOD (published wheels, clean uvx envs): 7/7 PASS** -- ledger round-trip (wrong-path release now RELEASES, footgun eliminated) · unscoped refuse exit-2 in 1.7s (was 60s timeout) · install-dense hints · doctor autostart honesty · prepare hint/--out/evidence-chain/symlink-refusal · WSL probe symptom ABSENT · dynamic-import decoys excluded. **GPU publish = adjudicated HOLD** (read-only decision package, every claim cited: "beats CPU on WSL/Windows search" is CONTRADICTED by every measured artifact; kernel corrected to brute-force byte-compare NOT PFAC; #169 is task-store framing not a GitHub issue; options (i) flip / (ii) gated-experimental + 2 named messaging fixes / (iii) hold -- recommendation (iii), CEO's call). Also this session: **#701** killed the 2-release index-lock flaky permanently (scheduler-independent Event-handshake contract test) after it red-ed the v1.92.2 release (decoded + rerun --failed recovery). **Banked follow-ups (PR comments):** scoring-prefilter fuzzy-match of unresolved literals into blast-radius affected_files (pre-existing; own slice + pinned ranking test) · no-ignore-family field mirroring + bounded-probe cost pin (#702) · `_agent_gpu_tg_command` shutil.which pre-resolution (#704) · stale citation in #705's region + the in-repo ledger-skill question (adding one requires the skill-index sync test + AGENTS/CLAUDE index updates). **CEO desk:** #72 benchmark-publish · #169 GPU (decision package on file) · #255 moat-options (multi-day cross-language) · #189-fork · #240-opt2.
- **Live PyPI: v1.92.1 (2026-07-21); v1.92.2 (#699) publishing at reconcile time — verify `/simple`/`gh run list` before citing it live. TWO campaigns this session drained one-per-publish, ZERO broken releases: the v21 world-class-readiness tier (#249) and the CEO deep-research "steal-list" directive (#251, now CLOSED).** World-class wave: ledger-CI (#689) · opt-in agent-accuracy golden gate (#690, a loop-4 measurement tool) · **hard cold-path SLA #691/v1.91.1** (bounded the #222 quadratic reverse-import BFS +4 siblings, 26.6s→9.5s; Opus-gate caught a 4th un-gated BFS on the callers path). **CEO deep-research campaign — 6 paper/tooling "steals" VERIFIED against the real code, 5 production improvements + a guard shipped, each independent-Opus-gated (a72885ce/a9d8458/a5438582/ab857cc):** **#693/v1.91.2** loop-4 CLI-dispatcher ranking fix (#250; the #690 gate surfaced it, accuracy 15→16/16) · **#694** many-pattern dedup guard (test-only; found a latent native aho-corasick over-count that blocks fast `-e/-f` delegation) · **#695/v1.91.3** intra-file rayon parallel search on the `backend_cpu.rs` FFI fallback path (line-aligned ≥50MiB chunks, byte-identical to serial) · **#696** accuracy-gate per-task pinning (#252; `assert not misses` replaces a floor that silently absorbed single-task regressions) · **#697/v1.92.0** CodeAnchor-style inline caller annotations (default-OFF `TG_CAPSULE_INLINE_CALLERS`, +2.8% tokens, found+fixed a DAR line-offset off-by-one) · **#698/v1.92.1** chunk-parallel binary-detection parity (#253; `search_file_chunk_parallel` was hardcoding `binary_detected:false` → raw byte matches on >64KiB binaries; mirrors the pinned grep-searcher 0.1.16 64KiB floor) · **#699/v1.92.2** Blackbird flat-scorer hardening (#254; exact word-boundary bonus + best-effort test-file demotion, provably non-destabilizing). **HONEST RESEARCH VERDICT (the CEO deliverable):** every "cheap win" the papers advertised came back NEGATIVE / big-refactor / secondary-path / MODEST once verified vs real code — cAST rejected (24x slower, quality-wash), dense-int8 memory-only + ~2x slower in numpy, warm-session a big refactor (the daemon holds a symbol-map, not a search-index; the common `tg search` is raw rg-passthrough), single-file only a fallback speedup (the headline 200MB tie is `native_search.rs`, streaming-serial-LOCKED by a tested ≥25ms first-match contract), ranking no golden-set movement. **The genuine moat gains are all multi-day CROSS-LANGUAGE efforts — native int8 kernel / native dedup+FFI / `execute_search`-extract+daemon-search or a PyO3 `TrigramIndex` binding / cuVS GPU — banked as #255, CEO-prioritize.** **CEO desk (unchanged + #255 added):** #72 benchmark-publish (public/irreversible) · #169 GPU (>$100 spend) · #48 native front door (~30-40ms Python floor) · #189-fork (taste) · #240-opt2 native wheels (distribution decision) · **#255 moat-investment options** (the deep-research follow-up — which multi-day cross-language effort, if any).
- **Live PyPI: v1.90.0 (2026-07-20); v1.91.0 (#685-#687) still publishing at reconcile time -- verify `/simple`/`gh run list` before citing it live. The CEO `/goal` "make tg REQUIRED vs rg/ast" 9-point campaign (#232) fully drained -- all 9 CEO gap-points mapped to a shipped release, PR queue EMPTY, drain CLEAR, ZERO broken published releases:** **CEO#9** GPU-honesty (`tg calibrate --json` structured `calibration_status` skip signal on a CPU-only build, #678/v1.84.0) -> **CEO#1** never-empty best-effort-primary under deadline truncation (`partial_primary` + a structural `confidence<=0.55` cap, #679/v1.85.0) -> **CEO#4** bidirectional-oracle exit-code completeness gate + `callers` likely-first parity (#680/v1.86.0) -> **CEO#8** enterprise close-the-loop (`EvidenceReceipt` -> `review-bundle --receipt` -> `verify --against` PR-head + `--min-receipts`/`--expect-key` policy enforcement, closing an empty-bundle bypass, #681/v1.87.0) -> **CEO#5** `tg prepare` one-shot edit-readiness CUJ (#682/v1.88.0) -> **CEO#6** AST parity that doesn't fight ast-grep (empty-result remediation + resolve-only ruleset aliases + honest sg-absent error, #683/v1.89.0) -> **CEO#2** mega-repo advisory auto-narrow (`workspace_root_detected` + proactive `suggested_scope`, NEVER a silent narrow, #684/v1.90.0) -> **CEO#7** `tg install-dense` one-shot packaged dense-embedding install, bundled with CEO#3's $0 doc-honesty fix (pip/uvx pays the Python-interpreter floor, #48; `tg upgrade` gets the native front door) and a calibrate-stdout-contract test nit (#687+#686+#685, all releasing as v1.91.0). **Two headline fixes BINARY-VERIFIED** via a clean-room `uvx --from tensor-grep@1.87.0 tg ...` dogfood: the GPU-calibrate structured skip on stdout, and gap#2's truncated-agent emitting a real `primary_target` (never null). **CEO desk (unchanged):** CEO#3-architectural native front door = **#48** (an open GitHub issue; ~30-40ms Python-interpreter floor); CEO#9-CUDA compute build = **#169** (>$100 spend); **#72** benchmark-publish (public/irreversible); **#240-opt2** per-platform native wheels (public-distribution decision) -- the latter three remain task-store framing, not open GitHub issues.
- **Live PyPI: v1.83.0 (2026-07-20, published clean). The CEO `/goal` "ultimate agentic toolkit" campaign (#224) shipped every AI-actionable pillar; PR queue EMPTY, drain clear, ZERO broken published releases:** the on-moat **A2A `tg ledger`** plane is live and dogfood-verified on the published binary -- **claims** (advisory code-scoped locks, always exit-0 + `overlaps`, TTL-prune; #673/v1.82.0; #225 dogfood: agent-b sees agent-a's overlap in production) + **findings** (content-addressed reuse with revision-freshness + integrity tamper-detect; #675/v1.83.0; #227 dogfood), both EXPERIMENTAL/default-inert, each independent-Opus-gated, composing only existing primitives (no new crypto/transport/bus). The deadline-SLA wave (#668-#672, v1.81.17-.21) closed the CEO-dogfood enterprise-scale gaps -- headlined by **#671/v1.81.20**, a super-linear vendored-subtree `resolve()` dedup (90-144x, ~61% of `tg agent` wall) that the v19 real-workspace dogfood surfaced AFTER #669's synthetic-scoped tail fix (**#222 -- synthetic sets don't carry magnitude**), plus `importers` likely-first bounded scan (#670), `route-test` SLA-under-load (#672), and the queued LSP follow-ups (#668). **#674/v1.82.1** bounded `tg codemap`'s git-identity/`resolve()` storm (its gate caught a `--check` TypeError CI would have shipped). **Creative-GPU ideation** produced 3 amortization-passing Tier-A ideas, all build-gated behind #169's spend. **CEO desk (unchanged except #77 A2A now DONE):** #72 publish the moat numbers (public/irreversible; verified + ready), #169 GPU-compute build (spend), #189-fork query-gated signal channels vs accept-the-ranking-ceiling (taste), #48 native-front-door (the ~30-40ms Python-interpreter startup floor). Demand-gated: #98 MCP-consolidation, #141 native-AstBackend. **#207 (stale local checkout) stays inert; #219 (torch/CUDA-13 bump) waits on RAPIDS shipping a CUDA-13 `cudf-cu12`.**
- **Live PyPI: v1.78.0 (2026-07-16, published clean). The `tg find` campaign (#189) SHIPPED end-to-end this session -- the CPU semantic moat / ColGrep response, the forward direction after GPU-for-search retired (#169):** whole-repo natural-language code search (BM25 + local CPU dense embeddings -> weighted RRF -> optional MaxSim -> budget-fitted file:line). Built via Fable plan -> 4-lens adversarial review (correctness/security/eval-integrity/architecture, unanimous GO-WITH-MUST-FIXES, each citing file:line) -> 3 TDD build waves + an MCP tool -> golden gate-run validation -> live dogfood, all cloud Agent subagents + GitHub CI (zero local CPU per the shared-server rule). **Per-wave receipts:** Wave 2a extracted the `rank_chunks` shared fail-closed core from `rerank_hybrid` (#624, `2393a7e`, byte-identical, Opus SHIP). Wave 1 built the T8 golden harness (`benchmarks/eval_late_rerank_quality.py`), a 40-query NL vocab-mismatch golden set, a 74-file corpus, and the P5 lane (#625, `d6fa824`, `chore(bench)` = no-release, bidirectional-oracle). Wave 2b/2c shipped the `tg find` CLI command -- registered at all sites, wired walk->chunk->legs->rank_chunks->budget-fit, with a fail-closed matrix (`BackendExecutionError`->exit-2 catch, chunk-cap->`result_incomplete`+exit-2, hand-written exit codes) (#626 -> **v1.77.0**, `501dc26`). Wave 2d shipped the MCP `tg_find` tool (agent-callable) as its OWN PR to de-risk the LLM-facing surface -- confine-root-first, an error-sanitization split, harness_api docs, and a contract-version bump (#627 -> **v1.78.0**, `6d79945`). **The gates earned their keep -- CI-green does not mean contract-correct, and they caught 2 real bugs, not nits:** the Wave-2c Opus gate caught a genuine F1 fail-closed violation (a query-time `DenseUnavailableError` would have crashed instead of BM25-degrading; fixed RED->GREEN, `045fadc`); the dual-Opus MCP gate caught a required contract-version bump the plan had missed (1.2.0->1.3.0, fixed `3fcca06`). **VALIDATION (INTERNAL; publishing stays CEO-gated #72):** the golden gate-run shows `tg find`'s hybrid ranking (rrf) beats plain BM25 by **+0.195 ndcg@10 (0.305 vs 0.109) / +0.30 recall@10 (0.55 vs 0.25)** on the 40-query NL golden set, positive in all 4 categories and essentially wins-or-ties per query (a single ndcg loss out of 40), bidirectional-oracle-validated twice, deterministic. Live dogfood of the published v1.77.0 wheel PASSED (real `uvx` wheel: `find` registered and not misrouted, honest BM25-only degrade when the `semantic` extra is absent, real relevant results for an NL query, exit 0). **IN FLIGHT: Wave 3 dense-weight knob (#628, still an open draft PR, checks green so far, not yet merged)** ships `TG_FIND_DENSE_WEIGHT` DEFAULT-OFF (1.0 = byte-identical no-op) plus a query-adaptive rule (queries over 2 `split_terms` tokens get the env weight; 2-token-or-shorter queries always stay at 1:1) plus a 10-query literal-query golden slice -- evidence infrastructure for the design pass's finding that a 1:5 bm25:dense weighting lifts NL ndcg@10 by +0.14 (0.305->0.4466) with zero per-category regression, while the literal slice stays protected by construction. Opus-gated SHIP-WITH-NITS, with 2 nits to close before any default-flip: a `math.isfinite` clamp on malformed `TG_FIND_DENSE_WEIGHT` input, and a 3-token-identifier re-sweep (multi-segment identifiers like `getUserName` classify as NL under `split_terms`). **The default-flip itself is a separate CEO checkpoint** (product taste; changes shipped ranking; evidence will be in hand once #628 lands). **Wave-4 stays HELD/evidence-gated:** `TG_LATE_RERANK` remains off -- the gate-run shows rrf+maxsim regressing vs bm25, but that is entangled with a known harness simplification (the late-rerank doc-role encoder is not query/doc role-aware yet, `retrieval_late.py:328-333`), so it is NOT a verdict on MaxSim itself; do not flip until role-aware encoding lands and it is re-measured. `TG_RRF_CHANNELS`/`TG_CHUNKER` remain evidence-gated too. **PR queue: 1 open** (draft #628). **CEO desk:** #72 publish the moat numbers (public/irreversible -- now covers both the original P1/P4 tokens-per-correct proof and this NL-search gate-run, verified + ready, still held); the dense-weight default-flip (product taste, pending #628 + evidence review); #77 tg-ledger; GPU retired-for-search (#169). Demand-gated: #98 MCP-consolidation, #141 native-AstBackend.
- **Live PyPI: v1.76.13 (2026-07-16, published clean). The last AI-actionable item shipped as its own honest close-out -- ZERO broken releases:** #182 (the 3 SHIP-WITH-NITS Opus-gate follow-ups from #612 GPU-calibrate honesty) had been deferred as "opportunistic-batch, do NOT fire standalone." With the drain clear and no future GPU-calibrate PR coming to batch into (the GPU program is CEO-held #169), that deferral would have let real honesty fixes rot -- so #182 shipped as **v1.76.13 #621** (a one-time close-out that empties the queue is closure, not tail-churn). **NIT-1 (the real fix):** the Python `tg calibrate` no-binary message still name-dropped `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia` in a "confirm before relying on" aside -- asymmetric with the Rust side (`crossover.rs::detect_device_name`), whose test forbids that override as an obtainable path (no nvidia asset ships). Dropped it; added the symmetric `FLAVOR not in output` assertion (RED->GREEN). **NIT-3:** "so calibrate can run" -> "that calibrate requires" (calibrate still fails-closed on a CPU-only box post-upgrade). **NIT-2 (`crossover.rs`, comment-only):** the `#[cfg(feature="cuda")]` mirror-TEST fn is compiled by NO CI job (`cuda-feature-check` omits `--tests`; `test-rust-core` is cuda-off) -- the "Compile-checked only" comment overstated coverage; corrected to state the real gap (the production fn IS compile-checked via its `:533` call site; only the test assertion is uncovered) + why `--all-targets` is deferred (pre-existing cuda test debt in `main.rs`/`test_routing.rs`). **All text-only -- no logic, no control-flow, no CI-config change.** **Adversarial Opus gate: SHIP-CLEAN** -- every honesty claim independently verified TRUE against the shipped assets (default release profile `native-frontdoor` = CPU-only; nvidia legs `if:`-gated off; PyPI wheel carries no CUDA) + no stale assertion elsewhere + zero regression. **Non-blocking coupling banked on #169:** if the GPU release flag ever flips to `native-frontdoor-gpu`, BOTH this message ("not shipped in any current build") and the Rust mirror test ("not shipped in this build") must update in the same change. **PR queue EMPTY (0 open). AI-actionable backlog EMPTY.** **CEO desk unchanged:** #72 publish (public/irreversible; verified numbers ready), #77 tg-ledger, #169 GPU held; #98/#141 demand-deferred.
- **Live PyPI: v1.76.12 (2026-07-16, published clean). The #72 benchmark MOAT RE-PROOF + the correctness fix it surfaced, ZERO broken releases:** The idle drain was put to the highest-value strategic use — re-running the CEO-flagged **#72 tokens-per-correct benchmark** now that **#460** shipped the scoped `tg imports`/`tg importers` primitives. The 2026-07-08 harness + express corpus survived in `scratchpad/bench/` (deterministic, **$0 — no model API**), so the re-run was internal (running is NOT gated; only public *publishing* is CEO-gated per the benchmark skill). **RESULT (independently re-scored via aggregate.py): P4 file-deps tokens-per-correct 53,631 (whole-repo `tg map`) -> 2,387 (scoped) = from ~10x WORSE than rg -> ~2.24x BETTER**, F1 preserved+improved (0.542->0.606, bidirectional oracle PASSED 25/25); P1 def-lookup still 6.4x better (tg 1,457 vs rg 9,328). **The moat is now proven on BOTH axes** — the P4 weakness the original benchmark exposed is closed. The re-run also surfaced a genuine correctness gap -> **v1.76.12 #619** `tg importers` now resolves directory-index imports (a file doing `require('./router')` — Node resolves to `lib/router/index.js` — is now found as an importer; express repro `importer_count 0 -> 2`). Confined to `tg importers` ONLY via `_reverse_importer_extra_aliases` (the shared `_module_aliases_for_path` is byte-identical to main, so `tg blast-radius`/ranking/PageRank untouched). **Opus gate SHIP-WITH-NITS -> remediated** (softened a false "cannot create a false-positive" comment + documented/tested the bare-specifier 0.2-conf heuristic; confined + a blast-radius non-inflation regression test) — and the remediation itself CAUGHT + fixed a PageRank regression in the gate's OWN suggested confine. **PR queue EMPTY (0 open).** **CEO desk:** #72 publish is the CEO's call (public/irreversible) — verified numbers ready; #77 tg-ledger, #169 GPU held; #98/#141 demand-deferred; #182 LOW-batch.
- **Live PyPI: v1.76.11 (2026-07-16, published clean). Post-v1.76.10 dogfood/hygiene follow-ups — 1 WSL-honesty fix + 1 latent release-gate flake, ZERO broken releases:** v1.76.11 **#617** `device_detect.get_platform()` now detects WSL2 via a 3-signal `_running_under_wsl` (env `WSL_DISTRO_NAME`/`WSL_INTEROP` -> `/run/WSL` -> `/proc/version` "microsoft", fail-closed) instead of `/run/WSL`-only — so a stripped-env WSL host reports `platform:"wsl2"` not `"linux"` in the `tg devices` GPU inventory (same WSL/GPU-honesty theme as #612/#615; closes the `device_detect.py` /proc/version sibling nit). **Opus gate SHIP-WITH-NITS** — all 5 safety claims verified against real code (`Platform.WSL2`/`LINUX` has NO control-flow consumer, only a report string at `device_inventory.py:63`; layering-clean core-must-not-import-cli; logic byte-identical to `is_wsl_host`; tests RED-GREEN + CI-safe) — the one drift NIT closed in-PR with a parity test pinning `_running_under_wsl == is_wsl_host`. **#616 (no-release, `test:`+docs)** fixed a LATENT release-gate flake: `test_empty_invocation_fallback_help_matches_public_contract` flipped PASS/FAIL on a BYTE-IDENTICAL binary because it parsed clap's fallback help and clap renders the `update` visible_alias width/platform-dependently -> switched to an INVARIANT assertion (all real cmds present + no unexpected + known aliases optional). Root-caused by BUILDING the real origin/main binary after a wrong first hoist-guess failed CI (lesson: [[tensor-grep-clap-help-parse-width-fragile-2026-07-15]]); the docstring softening + v1.76.10 ledger reconcile rode in #616 too. **#617's first CI red was a stale-base artifact** (branched pre-#616) — fixed by rebasing onto main, not a code defect. **PR queue EMPTY (0 open).** **AI-actionable backlog EMPTY** — remainder demand-deferred (#98/#141), CEO-gated (#72 benchmark, #77 ledger, GPU flip/Phase-2), LOW-batch (#182).
- **Live PyPI: v1.76.10 (2026-07-15, published). CEO v1.76.9-dogfood follow-up — one real fix after a corrected misdiagnosis:** v1.76.10 **#615** `is_wsl_host()` gains the canonical `/proc/version` "microsoft" fallback (Opus SHIP-WITH-NITS + WSL-verified end-to-end) — closes the all-signals-stripped WSL detection-miss behind the CEO's `failed_probe_path` residual. **CORRECTION BANKED (`tensor-grep-verify-code-against-origin-not-stale-local`):** the WSL path-*bridging* bug I first chased was ALREADY fixed v1.75.1 (#594) — I misdiagnosed it by grepping the STALE local checkout (47 behind, v1.74.0) + a manual raw-binary test that BYPASSED tg's translation; the build agent caught it via verify-against-origin/main BEFORE any code (no churn, #184 closed). **BIG UNBLOCK this session:** got WSL repro access (`wsl.exe -e bash`) — the WSL cluster (#89/#90) is no longer env-blocked; reproduced the CEO's failures NATIVELY (unscoped fast-refuses exit 2, GPU reports honestly) = 9p transients, NOT bugs. **2 LOW WSL nits ride forward:** the is_wsl_host docstring softened (this reconcile); `device_detect.py:278` has the same `/run/WSL`-only gap (theoretical — devices already detect; batch-with-future-GPU-touch). **AI-actionable backlog EMPTY** — remainder demand-deferred (#98/#141), CEO-gated (#72 benchmark, #77 ledger, GPU flip/Phase-2), LOW-batch (#182/#186-nits).
- **Live PyPI: v1.76.9 (2026-07-15, published). Post-#176 hardening + dogfood wave — 4 more PRs, ZERO broken *published* releases:** v1.76.7 **#610** gate-NIT hardening (session-daemon metadata coercion-safe removal via `_daemon_identity()` on both sides + Rust `create_checkpoint` fail-closed cleanup `remove_dir_all` on write-failure; Opus SHIP-WITH-NITS) · v1.76.8 **#611** checkpoint snapshot **SECURITY** — no longer follows symlinks (out-of-root file-disclosure): recreate-as-symlink instead of `std::fs::copy`, undo fail-closed via `_resolve_within_root` (Opus SHIP; F1 comment-accuracy + F2a Windows `ERROR_PRIVILEGE_NOT_HELD` message MUST-FIXes addressed + re-verified RED-GREEN) · v1.76.9 **#612** GPU `tg calibrate`/`doctor` guidance honest when this build ships no nvidia asset (CEO v1.76.6-dogfood ask — conditions on the Rust `#[cfg(feature="cuda")]` compile flag, splits the shared hint into no-cuda-build vs device-not-found so an nvidia-binary user is never told "not shipped"; Opus SHIP-WITH-NITS = #182) · **#613** widen the flaky `test_index_lock` heartbeat timing bound 0.6->2.0s for loaded CI runners (`test:` no-release; RED-GREEN verified 0.064s green vs 3.977s sabotaged). **PR queue EMPTY (0 open).** RELEASE-FAILURE NUANCE reinforced: v1.76.9's FIRST run FAILED on that timing-flaky heartbeat test (Semantic Release SKIPPED, no tag, PyPI not bumped) — a job-failure release does NOT self-heal (distinct from a push-race rejection), `gh run rerun --failed` cleared it (flaky passed on retry) and #613 hardens it against recurrence. **#90 CLOSED** — ast-grep "doctor false-available (exit-127 shim)" verified already-fixed in #130(b) (`is_available()` probe-RUNS each `which()`-resolved candidate via `ast-grep --version`, gates on exit 0); native dogfood confirmed. **AI-actionable backlog EMPTY** — remainder demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89 WSL /mnt/c path, needs Linux), CEO-gated (#72 benchmark publish, #77 tg-ledger, GPU flag-flip held/Phase-2), or LOW opportunistic-batch (#182 = #612 gate NITs).
- **Live PyPI: v1.76.6 (2026-07-15, published). Directive #176 ("implement the remaining AI-actionable backlog") COMPLETE + a dogfood follow-up (#608) — a 7-PR wave, Sonnet-TDD in `isolation:'worktree'`, Opus-gated where load-bearing, drained one-per-publish, ZERO broken releases:** v1.76.0 **#601** promote `tg route-test` hidden->public (also closed a native-front-door gap — route-test was absent from the rust front door; dogfood-verified on the wheel) · v1.76.1 **#602** checkpoint/rollback write symlink-hardening (Opus SHIP — genuinely TOCTOU-safe incl. Windows `FILE_FLAG_OPEN_REPARSE_POINT` same-handle check, NOT the #110 O_NOFOLLOW-noop) · v1.76.2 **#604** perf `@lru_cache _expected_tg_version` + `tg importers` dead-provenance precision fix · v1.76.3 **#603** session-daemon removes only its OWN metadata (stale-daemon orphan-pileup guard; Opus SHIP-WITH-NITS) · v1.76.4 **#605** bound the cuda GPU implicit-walk to mirror the #105 native DoS ceiling (Opus SHIP-WITH-NITS, exact parity + fail-closed) · v1.76.5 **#606** `tg orient` `suggested_scope` excludes deweighted/ignored trees (no longer misdirects agents to `.claude`; dogfood-verified agent-studio `.claude`->`scripts/`) · v1.76.6 **#608** `tg agent`/`context-render` `suggested_scope` excludes ignored trees too — the #606 SIBLING that dogfooding the SHIPPED v1.76.5 wheel caught (tg agent STILL misdirected suggested_scope to `.claude` while suggested_ignore excluded it; CI + the #606 review both missed it; dogfood-verified before/after `.claude`->`scripts/`). **PR queue EMPTY (0 open).** One CI hiccup self-corrected: v1.76.3 hit a transient Windows dep-install flake -> `gh run rerun --failed` cleared it (a job-failure release does NOT self-heal, unlike a push-race rejection — banked). Cleanup done (6 agent worktrees + all branches pruned). **AI-actionable backlog is now EMPTY** — remainder is demand-deferred (#98 MCP-consolidation, #141 native-AstBackend), env-blocked (#89/#90, need Linux/WSL), or LOW nits (#178/#125; #179 shipped as #608). DOGFOOD LESSON reinforced: running the SHIPPED wheel after a fix catches sibling gaps that CI + the fix's own review miss — #179 was found dogfooding v1.76.5.
- **Live PyPI: v1.75.4 (2026-07-14, published).** The GPU Phase-0 program drained one-per-publish, ZERO
  broken releases: **v1.75.0** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/
  skill trees (M1+M2, a CEO-dogfood-found gap in #164's `.claude` deweight) | **v1.75.1** #594 GPU
  Phase-0 P0-1 WSL probe path-domain bridging + a `cargo check --features cuda` anti-bit-rot CI gate |
  **v1.75.2** #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation |
  **v1.75.3** #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer
  downgrade | **v1.75.4** #597 GPU Phase-0 gate-nits (**#172**): doctor-probe precision + native
  error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix that silently skipped 3
  tests under a default `cargo test`. Together this closes out **#171** (the GPU Phase-0 program) --
  full receipt in CURRENT LIVE BACKLOG below. **HONEST SCOPE (council must-fix MF-3):** this wave
  hardens the CPU-default GPU code path's correctness/observability under the existing default-OFF
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` gate -- it does NOT promote GPU, change the CPU-default
  recommendation, or prove a speed crossover; full reframe in CEO-FACING GPU below. **#592** (prior
  docs reconcile to v1.74.x) merged `adf5750`; the PR queue was empty going into this wave and is empty
  again after it (see SHIPPING below).
- **Prior wave: v1.74.4 (2026-07-14, published).** The v1.73.0->v1.74.x
  wave — the CEO's v1.72.1 dogfood tail + the v1.74.0 WSL-saddle dogfood fix-queue (#164) — drained
  one-per-publish, ZERO broken releases: **v1.73.0** #584 `tg edit-plan` top-level `confidence` +
  `ask_user_before_editing` (agent parity) & #585 `--deadline` on source/docs-coverage/blast-radius-plan ·
  **v1.74.1** #131-F3 fail-closed `GpuSearchParams` flag completeness (replace/only-matching/max-filesize/
  color/no-ignore-vcs + `context`) · **v1.74.2** #164 embed mermaid in JSON when `--json --mermaid` combined
  (was: `--mermaid` silently dropped under `--json`) · **v1.74.3** #166 clean error + exit 2 for explicit
  `--gpu-device-ids` with no GPU backend (was a raw `ConfigurationError` traceback) · **v1.74.4 (releasing)**
  #164 `tg orient` deweight `.claude` tool-config trees + populate `suggested_ignore` (real-corpus validated:
  agent-studio 10/10 `.claude` in top-10 central_files -> 0/10; tensor-grep byte-identical). **HONEST
  CORRECTION (dogfood-the-shipped-artifact):** F3 (v1.74.1) hardened the rust GPU path, but dogfooding the
  live wheel proved `tg --gpu-device-ids` is handled ENTIRELY by the Python `Pipeline` (selects CuDF/Torch
  backend or raises `ConfigurationError`) and NEVER invokes the rust `handle_gpu_search` — so F3 is CLI
  dead-code. Corrected to the CEO, closed #131/#165, filed the real UX fix as #166 (shipped v1.74.3). **CEO
  1.74.0 dogfood FULLY addressed:** --mermaid (v1.74.2), GPU traceback (v1.74.3), orient-deweight (v1.74.4);
  session_id absence = not-a-bug (uniformly absent across agent/orient/callers, filed LOW observability);
  WSL timeouts = 9p artifacts (native-repro'd, complete). **#591** (`chore(test):`, no release) widened
  timing headroom on 2 flaky sidecar-IPC timeout tests (#167) — MERGED (`fc231ed`). **#592** (this docs
  reconcile) is the lone open PR (was branched from a stale local main at v1.74.0; rebased onto current
  main so its `pip-audit` sees the shipped setuptools 83.0.0, not the pre-bump 82.0.0).
- **Prior wave: v1.72.1 (2026-07-13) — the edit-plan/agent-parity + `--deadline` coverage wave, drained one-per-publish, ZERO broken releases, dogfood-verified where noted:** v1.71.3 **#159** `tg lsp` fail-closed with a clean "pip install tensor-grep[ast]" message on the missing `ast` extra (was a raw `ModuleNotFoundError` traceback; run `29281694988`) · v1.72.0 **#580** `tg edit-plan` structured top-level `validation_plan` (parity with `tg agent`; the CEO v1.71.1 dogfood ask #1) · v1.72.1 **#581** accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (the CEO v1.71.3 dogfood HIGH — the exit-2 "No such option" cliff that burned agent loops; dogfood-verified on the wheel: all 7 accept it, enforced, correct exit codes, orient stays exit-0 per its NO-exit-2 contract). **#582 merged (test-only, `test(cli):`, no release)** — closes PR #581's Opus-gate coverage gaps (daemon-skip regression test w/ passing mutation-check + real-truncation exit-2 + agent-2nd-scan + `CONTRACTS.md` `tg context` nit); full CI matrix green (`6cb53a4`). **PR queue now EMPTY (0 open).** Docs-only, no release, both merged: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).
- **Prior wave (v1.70.0-v1.71.2, 2026-07-13) — the v1.69.3-dogfood MED batch + audit sweep, drained one-per-publish, ZERO broken releases, all dogfood-verified on published wheels:** v1.70.0 **#152** sys.path.insert imports (2 HIGH) · v1.70.1 **#127** non-git `.gitignore` · v1.70.2 **#90b** `tg doctor` ast-grep exit-0 honesty · v1.71.0 **#153** `tg codemap` default deadline (agent-loop-safe) · v1.71.1 **#154** unscoped/multi-root search fast-refuse (<1s vs 60s timeout — enterprise gap #1) · v1.71.2 **#158** `tg scan` marked-root workspace refuse (the #154 sibling; verified on the wheel — fast-refuses a marked workspace parent). **#578** (docs, no release): 4-skill accuracy refresh correcting TWO false WSL-`/mnt/c` "regression" claims (whole-repo `tg agent` + `tg codemap` — native repro: agent ~26s, codemap 41s whole-repo `partial=false` complete). **CodeQL alert #13 (py/redos test fixture) resolved** (dismissed — false positive on a deliberate ReDoS fixture). **Moat FULLY dogfood-verified on real code** (orient / agent / `search --rank` / `--semantic` graceful-degrade / codemap + #158 scan) — all healthy.
- **Prior wave (v1.70.0) -- the CEO's 2 HIGH `sys.path.insert` fix (#152/#568, `feat` = minor bump), dogfood-verified on the published wheel.** CEO v1.69.3 dogfood found `tg imports`/`importers` did NOT resolve `sys.path.insert(0, .../lib)` path-hacked modules (`from ultrathink_routing import` -> `resolved=None`/`external=True`). Fix parses statically-resolvable `sys.path.insert/append` dirs as import search roots for BOTH the forward (`_python_imports_with_lines`) and reverse (`_python_imports_and_symbols`) resolvers in `repo_map.py`; dynamic/out-of-root exprs stay external (honest). **Verified live on the v1.70.0 wheel** (clean venv): forward resolves `.../lib/ultrathink_routing.py` (`external=False`); reverse `tg importers` -> `importer_count=1, importers=['main.py']`. The release recovered from a razor-thin timing flake in an UNRELATED perf test (`test_incremental_refresh`, missed the `<0.5x` bar by 0.0013s -- NOT a #152 regression): the rerun passed + `release-tag-smoke`=success on the wheel; **#569** (`6eaf384`, `test:`, no release) permanently de-flakes it (per-file sleep raised so the signal dominates the shared graph overhead). **DRAINING one-per-publish: #570** index `.gitignore` non-git-dir no-op fix (#127, `add_ignore` trio in `index.rs`, Opus-gate SHIP, 5 Rust tests) -> **v1.70.1**.
- **Prior wave (v1.69.3): #151 shipped (2026-07-13):** running the published wheel on 3 real external repos (flask/fastapi/requests) surfaced one genuine correctness gap -- `tg importers FILE [ROOT]` (ROOT defaults to CWD) returned an empty `importer_count` with NO signal when FILE is OUTSIDE ROOT (indistinguishable from "genuinely unimported"; silent-wrong for an agent shelling `tg importers /other/repo/file.py` from a different CWD). Fix (**#566** `00e4e99`, Sonnet-TDD -> **Opus gate SHIP** 7-axis adversarial, additive-only, MCP output-shape safe): a lexical containment check in `build_file_importers_from_map` stamps `file_outside_root` + an honest `scan_remediation`. **Dogfood-verified on the published v1.69.3 wheel:** outside-root -> `file_outside_root:true` + remediation; in-root -> `false` + correct `importer_count`. fastapi/requests batteries were clean (no new defects).
- **v1.69.0-.2 (prior wave):** **CEO v1.68.1 WSL-dogfood drain COMPLETE** (2026-07-13) - 3 genuine fixes built (Sonnet-TDD in `isolation:'worktree'`, Opus-gated where MCP-reaching), drained one-per-publish, **zero broken releases**, all **dogfood-verified on the published v1.69.2 wheel** (`release-tag-smoke` = success on the wheel): (a) **#562** `tg codemap --ignore` + `--deadline` (`codemap.py:862`, reuses `_apply_ignore_globs`; no MCP/backend surface) -> **v1.69.0**, both flags accepted + JSON emitted; (b) **#563** F2 nested-import recall (`repo_map.py` two `tree.body` -> `ast.walk(tree)` at :5827/:1813; `tg imports`/`importers` had silently missed function/class-scoped imports incl. the repo's own `main.py -> repo_map.py`; Opus SHIP) -> **v1.69.1**, verified nested `json`+`collections` now resolve alongside top-level `os`; (c) **#564** F3 `suggested_scope`-on-tie (`agent_capsule.py` new `_suggested_scope_from_tied_targets` :197, trigger :2375; the ambiguous-tie path now emits a narrowing scope (deepest common parent of the tied candidates) when they share a subtree, honest-null when the tie spans the whole repo -- both confirmed by dogfood; touches `tg_agent_capsule` MCP; **Opus SHIP** + gate-recommended `os.path.normpath` `..`-confinement hardening + probe test, 11/11 real-venv) -> **v1.69.2**, verified code+normpath-hardening shipped. **WSL-artifacts DEBUNKED (not chased):** codemap "60-180s/no JSON" = WSL 9p (native 33s complete); daemon "not warm" = a naive 2-run test that never hit cache (real ~90-150x cold->warm); env-blocked **#89/#90** need a Linux/WSL box.
- **Prior wave:** **Live PyPI was v1.68.2.** **Campaign #142 ("backlog-100") COMPLETE** — all 4 PRs drained one-per-publish, zero broken releases. **Post-campaign (docs-only, no release):** #559 backlog-reconcile + #560 AGENTS.md whole-repo ruff-scope hardening merged; local-git hygiene = 46 stale branches + 9 remote refs cleaned. Release-blocker learnings banked: `tensor-grep-whole-repo-ruff-format-gap-and-git-show-smudge-2026-07-12` (doc-code-block ruff-format + stale-lock rode into #553; hotfixed via #558) + `tensor-grep-windows-worktree-agents-mask-cross-platform-ci-2026-07-12` (#556 Windows-path tests failed Linux CI).
- **Campaign #142 4-PR queue DRAINED** (Sonnet-built, Opus-gated, one-per-publish): **#554** mcp default 512→2000 (#98) → v1.67.1 · **#555** daemon Tier-2 orient/agent (#108, ~16x latency — dogfood-verified 15.8s→0.95s on the PUBLISHED wheel) → v1.68.0 · **#556** apply_policy UNC-bypass + cross-platform test hardening (#126) → v1.68.1 · **#557** `--count-matches` honest-refuse (#121) → v1.68.2. The mandatory security/correctness gate caught+fixed PRE-MERGE: a UNC command-injection edge (#556), a contract-governance gap (#557), a cross-platform test hole (#556), and a daemon cold-rescue recall regression (#555).
- **Campaign #142 ("backlog-100")**: 4 Fable design-planner audits (`docs/plans/backlog-100/cluster-{1,2,3,4}-*.md`, 2026-07-12) re-verified this ENTIRE ledger, file:line-cited, against the real tree. Headline: **the ledger was badly stale** — most standing items were already shipped across 4 drain waves (#514–#537) that never got written back here. This refresh reconciles it.
- **Reconciled this campaign (already-fixed → dropped from the live backlog below; full per-item receipts in the cluster docs):**
  - **P0 #128/#130/#131 audit queue — 9 of 12 sub-items already fixed**, drain wave #514-#523: #128a ast-grep malformed-JSON→`BackendExecutionError` (`c9e54ef`/#515) · #128b nested-`.gitignore` in both Python walkers (`29269ef`/#522 + `5bf49ad`/#523) · #130a inventory `--deadline`→files=0 (`f88c2a0`/#516) · #130b `tg refs` "45s hang" **superseded/debunked** (deadline-bounded since #393/#478/#440; live repro = 9.16s, exit 2, `partial:true` — an honest partial, not a hang) · #130c checkpoint `IsADirectoryError` (`fad9c2e`/#517) · #130d doctor false `ast_grep.available` (`ac2e153`/#518) · #131 F1 PFAC doc claim (`1889a69`/#514) · F2 GPU benchmark `line_number` vs native `line` key (`7bbe15c`/#519) · F10 dead GPU code (`4a72fca`/#520). Only **#128d, #128c, F3** survive — see CURRENT LIVE BACKLOG. Cite: `cluster-1-p0-correctness.md`.
  - **#118** (#93 SUB-3 unscoped-refuse + SUB-2 companion) — fully shipped via `#506`+`#528`; the companion shipped as **`suggested_scope`** (the old ledger's "suggested_ignore" name never existed in code). **#130 features (a) validation_plan parity + (c) confidence-lift** — shipped via **`#475`** (`ae3ec6d`, v1.54.2, the #84 design). Only **#130(b) sys.path.insert** survives. Cite: `cluster-2-p1-moat.md`.
  - **#129** help-probe-timeout de-flake — closed, two independent control-run fixes (`#521` Python e2e + `#537` Rust sidecar-IPC). **#73** hygiene-guard blind spot (kvikio/dstorage readers) — closed, KEEP-AND-DOCUMENT shipped in `4a72fca`/`#520`. Cite: `cluster-3-p2-followups.md`.
  - **#22, #38, #44, #47, #48, #59, #62 — ALL CLOSED** (the 7 oldest ledger entries, PR3b-era through 2026-07-07): fixed, superseded, or re-homed on receipts (retention-cap #329/#427 · audit-manifest digest+verify system · lockfile #355/#376 · AST byte-budget cache #539 · render-flag guard · sidecar envelope #304 · version-soup structurally gated · daemon Tier-1 #492/#498 · recall+honesty wave #463/#504/#418 · exit-2 contract #419 · Go Stage-1 #420/#422/#431). **#38 (`tg diff-docs`) killed outright** — retirement line added to `PAPER.md` §3.10. **#63 converts to one small build item** (F19+F22+F26 lang-graph tail — see CURRENT LIVE BACKLOG). Full receipts: `cluster-4-stale-reconcile.md`.
- **Net effect:** CURRENT LIVE BACKLOG below is a full rewrite — every surviving item is re-cited against today's tree; #89/#90/#109 (Linux-blocked) carry forward unaudited (outside campaign #142's scope).
- **CEO-gated (the CEO's call):** benchmark publish #72 (the 7.5x-fewer-tokens-than-grep proof) · `tg ledger` #77 (local agent coordination) · GPU multi-week rebuild (conflicts with no-SaaS) · next-language expansion (Java/C#/C++/Ruby/PHP). See CEO-FACING below.
- **Strategic (standing CEO steer, still in force):** tool WORKS (moat = **7.5x fewer tokens than grep on definition-lookup**, benchmark-proven); finish the moat + shift to gotcontext wiring vs draining the self-refilling tail; no-SaaS (gotcontext.ai is the SaaS shell, not tg).

---

## OPEN FINDINGS — 2026-07-31 deep-dive audit (Wave-1+2)

Full register: `docs/audits/2026-07-31-tensor-grep-deep-dive.md`. Remediation:
`docs/superpowers/plans/2026-07-31-tensor-grep-audit-remediation.md`. Local checkout audited at
**v1.101.20**; live tip then **v1.101.22**.

| # | sev | finding | wave2 | status |
|---|---|---|---|---|
| #858 | LOW | `codemap._atomic_write_text` replaces symlink dest (integrity, not RCE) | VERIFIED (sev↓) | Ready-to-build |
| #859 | MED | Form-1 writer ratchet missing | VERIFIED | **SHIPPED 2026-08-05** (#937/#945/#946/#947) |
| #861 | INFO | Position bugs already fixed; shared-banner unify cosmetic | WEAKENED→INFO | Fold into #860; close as product gap |
| #860 | LOW | Disclosure docstring lie + tip stamp lag + Slice-2 CONTRACTS lie (DD-003) | VERIFIED | Ready docs |
| DD-001/#864 | LOW | Python relative `$file` can be dash-named; Rust absolute OK; MCP default-OFF | WEAKENED (sev↓) | LOW / Ready optional |
| DD-003 | MED | `CONTRACTS.md` Slice-2 path-literal lie vs `_ledger_physical_root` | VERIFIED | Fold into #860 |
| DD-008/#865 | MED | `--ltl` in bootstrap, absent from `rust_core` (clap-reject) | VERIFIED | Ready |
| DD-005 | MED | `--stats` Win vs Linux route divergence | VERIFIED (documented) | Open |
| #862 | LOW | GPU evidence argv missing `--` (paths always absolute) | WEAKENED | LOW |
| #863 | LOW | Daemon tokenless `is_authorized` fail-open | VERIFIED | **CLOSED** (2026-08-01 backlog campaign, PR-B: fails closed) |
| DD-002 | — | Audit-manifest Rust `O_NOFOLLOW` “gap” | **KILLED** | Comment rot only |
| #115/#125 | — | Listed open LOW | **KILLED** | Mark CLOSED |
| DD-004 | INFO | `cpu_backend` `RuntimeError` hygiene | WEAKENED | **RETIRED 2026-08-05** (bank AGENTS.md Fail-Closed Contract; see dated receipt above) |

Do **not** reopen: #276, GPU HOLD, cAST, free-threading, MCP `<2` cap.

---

## OPEN FINDINGS — 2026-07-29 codex audit of the implemented branches (#860, #862)

Recorded per the CEO's "document any new issues, bugs or findings in backlog.md". All four were
found by an EXTERNAL audit of code I had already written tests for and called done.

| # | sev | finding | status |
|---|---|---|---|
| F1 | MEDIUM | `paths_defaulted` means only "no positional PATH". A search scoped by `--glob`/`--iglob`/`--type`/`--max-depth` DID choose a scope, so the defaulted-scope note was a FALSE POSITIVE claiming the search covered the whole current directory. | FIXED in #862 |
| F2 | LOW | `--quiet` zero-result searches began writing an informational note to stderr where they had been silent — an unmentioned contract change on the one flag whose purpose is silence. | FIXED in #862 |
| F3 | LOW | Three of four "control arms" in the first cut still PASSED with the fix reverted; they exercised pre-existing helpers (`_requires_full_cli`, `_search_args_include_explicit_path`) instead of the new behaviour. A control arm that survives the revert is not a control arm. | FIXED — tests rewritten behavioural, revert-proof verified |
| F4 | LOW | The primary test asserted on `inspect.getsource(...)`, so it could pass with the predicate present but misplaced or emitting the wrong text. | FIXED — subprocess-based |

**ROOT-CAUSED (task #24, 2026-07-30) — `tg search --stats` routing DIVERGES BY PLATFORM, but the
divergence is ENVIRONMENTAL, not platform-conditional code.** `cli/main.py::search_command` has a
SECOND, internal rg-passthrough branch beyond bootstrap's own front door
(`can_passthrough_rg and stats and _selected_route_supports_rg_passthrough(...)`, `cli/main.py:8004-
8017`) that hands the whole search to a live `rg --stats` subprocess and `sys.exit()`s on its exit
code — skipping `_emit_stats()`, the `--debug` echo, and the `is_empty` defaulted-scope note
entirely. Whether it fires depends only on `Pipeline.selected_backend_name == "RipgrepBackend"`,
which depends only on whether `rg`/`rg.exe` is resolvable on `PATH` (`resolve_ripgrep_binary`,
`cli/runtime_paths.py:561-591`) — there is **no** `sys.platform`/`os.name` check anywhere in the
chain. The `test-python` CI job (`.github/workflows/ci.yml:361-430`) installs no ripgrep package on
any OS, so this reduces to an ambient fact about each runner image's `PATH`, not a code branch.
Paired-proof (same tree, `PATH` with/without a real `rg.exe`, Windows, 2026-07-30): with `rg`
resolvable, exactly the reported Windows CI symptom reproduces; with `PATH` stripped of `rg`,
`Pipeline` falls back to `CPUBackend` and the identical invocation reaches `is_empty` and prints the
note — exactly the reported Linux CI symptom. `--ast`/`--rank`/`--semantic` never take this branch
(categorically excluded in `_can_passthrough_rg`, `cli/main.py:5359-5363`) — `--stats` is the one
flag in the family with no such exclusion, which is why only it XPASSed. Full citation trail:
`tensor-grep-architecture-contract` SKILL.md, "A THIRD rg-passthrough door lives INSIDE
`cli/main.py::search_command`". One-line CI confirm:
`python -c "from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as r; print(r())"`
(expect a real path on one OS leg, `None` on the other, inside the `test-python` job). Not fixed —
both routes are contractually honest (exit 1, real output either way); this is a documented, not a
broken, dispatch shape. Xfail reason sharpened to point at this mechanism instead of "platform-
divergent" (`tests/unit/test_full_cli_route_names_its_scope.py`).

**NEW FINDING (task #24 sweep, not yet filed as its own PR) — `--quiet` is silently dropped by BOTH
of `cli/main.py`'s internal rg-passthrough branches (the plain one at `cli/main.py:7937-7943` and the
stats one above).** `RipgrepBackend._build_cmd` (`backends/ripgrep_backend.py`) never translates
`config.quiet` into rg's own `-q`/`--quiet` — zero mentions of "quiet" in that file. `tg search PAT
--stats --quiet` on a box with `rg` installed (most boxes) prints rg's live stats block (and any
matches) to stdout, breaking `--quiet`'s "no incidental output" promise that the slow path enforces
explicitly (`cli/main.py:8468`, `:8478-8480`). Untested (`--stats --quiet` appears nowhere under
`tests/`) and **not platform-specific**. Needs its own TDD cycle; out of scope for task #24.

**RESOLVED — the A1b class ratchet's 9 candidates were ALL false positives.** Triaged rather than
reported: every one carried 7-29 disclosure signals through a surface the matcher did not know
about. Two matcher defects, both fixed:

1. The disclosure list held only helper-function names. Real emitters disclose via literal banner
   text (`codemap`'s `PARTIAL:` prefix, `scan`'s `INCOMPLETE`) and via helpers the list omitted
   (`_docs_scan_is_unreadable_truncated`). A literal banner IS a disclosure surface; requiring a
   helper call would force every emitter through one function for the checker's convenience.
2. The read-matcher matched `payload["partial"]`, which also matches the ASSIGNMENT
   `payload["partial"] = True`. That flagged `_run_ast_scan_payload`, a payload BUILDER whose job
   is precisely to stamp the field — the disclosure belongs downstream. **A producer is not a
   presenter**, and a checker that cannot tell them apart reports correct code as broken.

Net: 0 real defects, and the ratchet now discriminates. Had these shipped as findings it would have
been 9 false P0s — worse than the gap the ratchet exists to close.


## RESEARCH ANSWERED -- F7 Task 11 (cross-file caller resolution) is worth building (2026-08-05)

The open question was whether cross-file resolution earns its cost, and the stated cheapest
decisive test was: measure how often the blast floor falls short on a REAL multi-file repo BEFORE
designing a resolver. Ran it.

**Corpus:** `omega-fusion/source/lucebox-hub-main/server`, 269 real C++/header files (not a
fixture, not synthetic -- a synthetic corpus manufactures whatever ratio its generator encodes).

| measure | value |
|---|---|
| function-like definitions | 1114 |
| symbols with >= 1 CROSS-FILE call site | **511 / 1114 (46%)** |
| call sites in-file | 2731 |
| call sites cross-file | **4664** |
| share an IN-FILE-ONLY extractor cannot see | **63.1%** |

**What this number is NOT.** Ground truth is a regex (`sym\s*\(`), so it also matches
prototypes, declarations, comments, strings, and same-named methods on unrelated classes. 63.1% is
an UPPER BOUND with known inflation, from ONE corpus in ONE language. Do not quote it as a product
claim.

**What it does establish.** The direction is not close. Cross-file call sites OUTNUMBER in-file ones
roughly 1.7:1, and nearly half of all defined symbols have at least one caller in another file.
Even halving the figure for regex inflation leaves cross-file as a large minority-to-majority of
real call sites. An in-file-only caller graph is therefore structurally incomplete on real C++,
not merely imprecise -- which is exactly what the `blast_radius_floor` consumers key on.

**Disposition:** F7 Task 11 is JUSTIFIED. Before building, tighten the ground truth (parse call
sites rather than regex them) so the design is sized against a defensible number, and repeat on one
Java and one C# corpus -- a single-language, single-corpus measurement is a direction, not a size.

### FOLLOW-UP MEASURED 2026-08-05: the parsed C# re-measure (the council's wave-2/3 sizing ask)

Both halves of that disposition, run. Call sites are now **parsed** by the product's own extractors
(`lang_csharp.csharp_imports_and_symbols` for definitions, `LanguageSpec.references_and_calls` for
call sites), so prototypes, comments and strings are excluded by construction rather than by caveat.

**Corpus:** `reveng-main/external/ga_sources/winsw/src`, 70 real C# files across four projects
(WinSW, WinSW.Core, WinSW.Plugins, WinSW.Tests). 0 parse failures.

| measure | C# (parsed) | C++ (regex, above) |
|---|---|---|
| distinct method definitions | 289 | 1114 |
| symbols with >= 1 CROSS-FILE call site | **134 / 289 (46%)** | 511 / 1114 (46%) |
| call sites in-file / cross-file | 235 / **509** | 2731 / 4664 |
| share invisible to an in-file-only extractor | **68.4%** | 63.1% |
| cross:in ratio | **2.17:1** | ~1.7:1 |

**The finding that matters: parsing did not shrink the number.** The C++ figure was labelled an
UPPER BOUND with known regex inflation, and the honest expectation was that a parsed re-measure
would come in materially lower. It came in **higher** (68.4% vs 63.1%), on a different language and
an unrelated codebase, with the 46% symbol share reproducing to the percentage point. Regex
inflation was therefore not what was carrying the C++ result, and the design's premise survives the
tightening it was conditioned on. Wave 2 (C#) is sized and justified.

**Java is NOT measured, and this is a gap rather than a result.** No adequate Java corpus exists in
this workspace -- the largest is `repowise-main/tests/fixtures/sample_repo/java_pkg` at **3 files**.
A 3-file corpus cannot measure cross-file call-site share; running it anyway would reproduce the
trap where the probe's INPUT carries the property being measured. Wave 1 (Java, PR #950) therefore
ships on the C++/C# direction plus the language's own import semantics, and a Java sizing number
remains OPEN pending a real corpus.

**Four instrument failures produced believable zeros before any of the above was true**, each
caught by a control rather than by re-reading the probe:

| the zero | cause |
|---|---|
| 0 definitions over 70 files | `except Exception: continue` swallowed 70 identical `TypeError`s -- C#'s `extract_imports_and_symbols` is `None`; symbols come from `lang_csharp` directly |
| `KeyError: 'csharp'` | `LANGUAGE_REGISTRY` is empty without `import repo_map`; registration happens there |
| 1 definition over 70 files | the name key is `name`, not `symbol` -- 392 functions collapsed into one `None`-keyed entry |
| `pending: 0` on 17 running CI lanes | `gh` reports an unfinished check's conclusion as `""`, not `null`; the filter tested `== null` |

The positive control that stopped it (`assert len(defs) > 100`) is the reason a number was reported
at all instead of a fourth confident zero.

## MEASURED 2026-08-05: `refactor:` CUTS NO RELEASE -- the title gate and the publisher disagree

CLAUDE.md warns that `scripts/validate_pr_title_semver.py` (the PR-title gate) and
`[tool.semantic_release]` (the actual publisher) disagree about `refactor`, and that this file has
had it wrong before. Now measured end to end rather than read:

| | verdict |
|---|---|
| `_RELEASE_INTENTS` in the title gate | `refactor` -> **patch** |
| `[tool.semantic_release]` in pyproject | no `patch_tags`/`minor_tags`/`commit_parser` override at all, so python-semantic-release DEFAULTS apply: `feat` -> minor, `fix`/`perf` -> patch. **`refactor` is not in either list.** |
| what actually happened | PR #939 merged as `refactor:` at 14:35Z. Run 31015992191 completed **success**. Main head stayed `77d21f9` with **no `chore(release)` commit**, and PyPI stayed at **1.107.0**. |

So a `refactor:` PR passes the title gate as a releasing change and then **ships nothing**. The
practical consequences, both of which have bitten this repo:

- A fix scoped as `refactor:` MERGES, closes its ticket, and never reaches users -- while every
  tracker reads "shipped". Committed is not shipped; merged is not released.
- Waiting for a `refactor:` merge to publish before the next merge is waiting for something that
  will never happen. The push-race gate must key on the PUBLISHER's tags, not the title gate's.

Derive rather than trust this table: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py`
for the title gate, and `grep -A14 '\[tool.semantic_release' pyproject.toml` for the publisher --
an EMPTY result there means defaults, which is exactly the case that surprises people.

## DEPENDENCY MAP -- what 'Active / buildable' actually means (measured 2026-08-05)

The canonical queue lists ten rows as `READY`. Premise-checking them shows the genuinely
start-now set is SMALLER, for reasons that are not defects but ARE blockers. Recorded because a
row reading `READY` invites a session to start it and discover the blocker after writing code.

| row | blocker | measured |
|---|---|---|
| **MCP-SURFACE** (Task 4) | depends on **Task 2C** | Task 4 is titled "bump contract 1.8.0 -> 1.9.0" but the live value is **1.7.0** (`mcp_server.py:138`, `_TG_MCP_SERVER_CONTRACT_VERSION`). Task 2C performs 1.7.0 -> 1.8.0. Building Task 4 first would bump from a version that does not exist. |
| **Task 2C** (and 2B) | needs CI or a cloud seat | modifies `rust_core/src/main.rs`; verifying it requires `cargo`, which AGENTS.md forbids on this shared dev box. Also needs a real WSL host for the `/mnt/c/...` path-domain arms. |
| **#89 / #90** | **CONTRADICTS the "Active / buildable" section — unresolved as of 2026-08-05** | this row files them as blocked "same as 2B/2C" (needs `cargo`/a real WSL host), while the Active/buildable rows call #89 "now `READY`, **not** environment-blocked". Both cannot be true. The distinction that likely reconciles them: REPRODUCING the defect needs a WSL host, but the FIX may be pure-Python path-domain handling on the Python side of the boundary — which would be buildable here. A build task dispatched 2026-08-05 carries a premise check as step 0 and is instructed to stop and report if the fix genuinely requires the Rust half. Its answer settles this row; do not act on either reading until then. |
| **Task 3** (#859) | ~~PARTIALLY DONE~~ **DONE 2026-08-05** | the CLASS census gap closed in PR #937 (3 -> 41 modules); the VIOLATING-sites half this row called open closed too -- #945 classified all 16 identities, #946 closed the download TOCTOU, #947 retired the last deferral. H2 is closed. |

**CORRECTED 2026-08-05 (same day).** The paragraph this replaces called F5 and F8 "unblocked and
genuinely start-now". That was wrong, and reading the plan's own file lists is what showed it:

- **F5 (Task 8)** and **F8 (Task 12)** both list `src/tensor_grep/cli/prepare_service.py` as
  MODIFIED. That module did not exist until PR #939 created it (Task 6 Step 0), and the plan says
  explicitly that Task 8 "must modify, not create, it". Both are gated on #939 merging.
- **F8 (Task 12)** additionally modifies `rust_core/src/main.rs` and `rust_core/src/path_domain.rs`
  and touches `tests/e2e/test_routing_parity.py` -- cargo and the e2e routing suite are both
  forbidden on this shared dev box, so its verification must run in CI or on a cloud seat.
- **REF-CALL-REGISTRY (Task 9)** is DONE in substance: `_references_and_calls_for_path` is four
  statements with zero language branching (the F7 campaign removed the ladders as a side effect).
  Its Step 2 guard was never written; PR #940 adds it. NOTE the canonical row MISLABELS Task 9 as
  "prepare-service extraction" -- the plan's Task 9 is the reference/caller dispatch registry; the
  prepare-service extraction is Task 6 Step 0.

So the true start-now set is currently EMPTY: everything waits on #939 or needs CI/cloud. That is a
real state, not a stall -- recorded so the next session does not start F5, write code against a
module not yet on main, and discover the ordering afterwards.

## H2 CLOSED 2026-08-05 -- writer-site classification complete, one residual RETIRED

The atomic-writer class is done. Measured from the ratchet's own scanner on main:

| | before (#937) | after |
|---|---|---|
| violating | 16 | **1** |
| helper-backed | 15 | 24 |
| sanctioned | 26 | 30 |

PR #945 classified all 16 individually -- 11 routed, 3 sanctioned with in-code reasons, 2
deferred. PR #946 then closed the first deferral: `_download_native_frontdoor_asset` claimed its
destination with `O_EXCL` and then RELEASED the claim before `urlretrieve` reopened the path BY
NAME. It now streams through the held fd. That path installs a downloaded executable.

**The last residual is RETIRED, not open.** `_ensure_node_runtime`'s `shutil.move` stays classified
violating, and that classification is correct -- but the item is retired rather than carried,
because the work it implies is not worth doing:

- The symlink+junction-aware `_remove_stale_staging_path` (shipped in #942) already runs at
  `lsp_provider_setup.py:404`, immediately before the move at `:411`. The residual window is the
  handful of statements between them.
- Reaching that window needs write access to `managed_provider_root` -- `~/.tensor-grep/providers`
  by default, user-owned. An attacker with that access has a SHORTER, TOTAL win already: drop a
  malicious `node-runtime/bin/node` and `_ensure_node_runtime` returns at `:361` without
  downloading anything. Nothing verifies a pre-existing runtime dir.
- `shutil.move` was chosen deliberately for cross-filesystem fallback; `os.replace` would silently
  break that, and no directory-safe atomic-publish primitive exists in the helper family.

So closing it would mean building a new primitive to shrink a window that grants no capability an
attacker does not already hold. **Reopen only on a concrete threat model** where
`TENSOR_GREP_LSP_PROVIDER_HOME` points under a world-writable parent (container volume, shared CI
cache) -- that configuration is neither the default nor documented, and `mkdir(..., exist_ok=True)`
would reuse an attacker-pre-created directory there.

## OPEN FINDINGS -- 2026-08-05, surfaced while widening the atomic-writer census (PR #937)

Both were found BY the #859 fix, neither was in its scope, and neither is fixed. Recorded per the
CEO's standing instruction to document new findings with a receipt.

| # | sev | finding | status |
|---|---|---|---|
| H1 | MEDIUM | **`shutil.move` at `lsp_provider_setup.py:356` is an unsanctioned publish site.** Unlike `os.replace` it CAN follow a destination symlink. It moves an extracted archive into `staged_dir` before the atomic swap-in at `:363`. Now censused (the ratchet maps `shutil.move`) and PINNED as violating -- deliberately not swept, because the surrounding `:361`/`:371` pair is a legitimate move-aside/rollback dance and a blanket rewrite would break the rollback. | OPEN -- pinned, not fixed |
| H2 | LOW | **16 pinned VIOLATING identities as a set.** Widening the census 3 -> 41 modules took violating identities 4 -> 16. Whether they warrant a fix wave is a scope decision, not a defect: `main.py`'s original 4 were pinned rather than fixed on the same reasoning. Each needs individual classification -- `session_daemon.py::_write_daemon_metadata_windows`, for instance, is a well-motivated hand-rolled re-derivation for Windows ACL reasons, violating only because a re-derivation is not the canonical helper. | OPEN -- needs a scope decision, not a sweep |

**Fixed in passing by #937, recorded because the shape recurs:** `_annotation_is_path()` was defined
and documented ("a Path-annotated parameter") but **never wired into `scan_function`**. A synthetic
probe of exactly the shape it describes -- `def publish(destination: Path, content):
destination.open("wb")` -- scanned to ZERO candidates. A documented-but-unreachable helper is a check
that cannot fail, and it was sitting inside the suite whose purpose is catching checks that cannot
fail. Wiring it in surfaced 3 previously-invisible sites in `lsp_provider_setup.py`, with the full
population diffed before/after to prove no regressions.

## OPEN FINDINGS -- 2026-08-04 waves 10A/10B (Java + C# caller-graph promotion)

Recorded per the CEO's "document any new issues, bugs or findings in backlog.md". Every item
below is an INSTRUMENT defect -- a check, probe, or git command that returned a believable
answer and was wrong. None was found by re-reading code; each fell to a control.

| # | sev | finding | status |
|---|---|---|---|
| G1 | HIGH | **A dated receipt was overwritten in place.** #927 rewrote the quoted command output inside a `Re-verified live 2026-08-01` receipt in `tensor-grep-enterprise-agent` to today's value, leaving the surrounding "still 5 parser-backed + 5 foundational" prose intact -- a self-contradicting sentence and a destroyed historical record. A receipt's value IS what the command printed on a date. | FIXED in #927: original quote restored, `SUPERSEDED` entry appended instead. #928 appends a second one rather than editing either. |
| G2 | HIGH | **I censused history from a branch carrying my own change**, then accused a past author of fabricating that receipt. On `origin/main` (positive control: file readable, 1 hit for the descriptor discussion) the 2026-08-01 quote is CORRECT for its date. The contamination pointed straight at a false conclusion because the branch/main diff was exactly the thing under investigation. | FIXED -- law recorded; run historical censuses against a checked-out `origin/main` worktree. |
| G3 | HIGH | **A `needs:`-gated CI job is ABSENT, not pending.** 13 jobs in `ci.yml` carry `needs: smoke`, so they have no check-run until smoke finishes. A settle gate of `all(bucket != 'pending')` is VACUOUSLY TRUE over the 11-check pre-smoke view, which structurally cannot contain any test lane. My own monitor had this defect and would have merged #927 on a view with zero tests executed. | FIXED -- gate now requires the heavy lanes to be PRESENT. Proven by the transition it was built to catch: 11 -> 39 check-runs the instant smoke finished. |
| G4 | MEDIUM | **A shallow clone manufactures a false "diverged history".** `git rev-list --left-right --count main...origin/main` read `2673 24`, `git merge-base` returned EMPTY, and `git pull --ff-only` said "Not possible to fast-forward". All four were artifacts of `.git/shallow`. After `git fetch --unshallow`: `origin/main` reachable 24 -> 2698, merge-base resolves to local main's own tip, and `main` IS an ancestor -- it was 25 commits behind, nothing more. Acting on the first reading means a force-push or a re-clone. | FIXED -- repo unshallowed; local main fast-forwarded to 2e7fc5a. |
| G5 | MEDIUM | **An `ast.walk`-based import check counts function-local imports as module-level.** Verifying "is `repo_map` imported?" in `test_session_cli.py` passed while the test still raised `NameError`, because `ast.walk` descends into function bodies and the file's three `repo_map` imports were all inside functions. The preceding `grep -c` was worse -- it matched the TEST NAME `..._reuse_repo_map`. | FIXED in #927 -- scope such checks to `tree.body`. |
| G6 | LOW | **A forbidden `git stash` was run by a build seat** in a repo with 11 live worktrees, which share one stash drawer. Recovered by popping the newly-created stash by index; the other agent's `perf/main-import-lazy-wave` stash was verified still present afterwards. No loss, but the prohibition is only as good as the replacement being named at the moment of temptation. | VERIFIED no loss; brief for future seats already forbids it explicitly. |
| G7 | HIGH | **A release landing mid-review reds EVERY open PR, and their own CI cannot see it.** `test_task_board_reconcile_stamp_is_not_many_releases_stale` compares docs/TASK_BOARD.md's stamp to pyproject's version. A PR tests against a base that PREDATES the release, where the stamp is inside tolerance; the release then ships, and the same commit is out of tolerance the moment it merges. Receipt: v1.103.0 published 21:06Z; run 30952799876 reddened main via #928 at 21:32Z; #930's 7 'failures' at 22:14Z were this one gate (6331 passed, 1 failed) and NOT a PHP defect. #929 showed MERGE-READY on a green that predated the release. No per-PR run can observe this by construction -- it is the union-collision law with TIME, not content, as the second slice. | main greened by #931; #929/#930/#932 rebased onto it. STRUCTURAL FIX OPEN -- see task #20; note a stamp==pyproject CI gate was DELIBERATELY REJECTED 2026-08-01 as over-eager, so the likely answer is for the release job to bump the stamp in its own chore(release) commit. |

**Cross-slice note (union law).** #928 was authored on #927's pre-doc-repair base, so its skill/doc
edits would have silently reverted G1's repair on merge. Caught by rebasing onto the repaired head
BEFORE opening the PR and running the union; neither branch's own CI could have seen it.

## SHIPPING — open PRs (drain one-per-publish) — task #117

**Queue empty -- 0 open PRs (verified 2026-07-24 via `gh pr list --state open`).** Since the prior
BACKLOG reconcile (**#735**, v1.98.1), the banked C known-limitation was closed and its C++ sibling
found and closed too: **#736** (v1.98.2) and **#737** (v1.98.3) drained one-per-publish, ZERO broken
*published* releases, plus two non-releasing PRs (**#738** docs, **#739** test de-flake) -- full
receipts in CURRENT STATE above. Before that, the top-10 symbol-graph language campaign
(**#723-#734**, v1.93.10->v1.98.1) drained the same way -- full per-release receipts in CURRENT
STATE above. This BACKLOG reconcile (`docs:`, no release) is the next PR to open -- drain clear, no
other build queued. **Next move is CEO-gated** (native front door #48, GPU-CUDA compute build #169,
benchmark-numbers publish #72, per-platform native wheels #240-opt2 -- see CEO-FACING below) or
demand-gated; no AI-actionable backlog item is currently queued.

**Prior drain waves:** the CEO `/goal` #232 9-point campaign (**#678-#687**, v1.84.0->v1.91.0)
drained one-per-publish, ZERO broken *published* releases -- full per-point receipts in the header
above. Before that: the CEO `/goal` "ultimate agentic toolkit" campaign (**#668-#675**, v1.81.17->
v1.83.0) drained one-per-publish, ZERO broken releases, un-gating A2A (`tg ledger`) + GPU ideation. Before
that: the senior-review + Rust-dogfood campaign (**#655-#666**, v1.81.6->v1.81.16); the v1.75.0->v1.75.4
GPU Phase-0 wave (#593/#594/#595/#596/#597) drained one-per-publish, ZERO broken releases, closing out
**#171** (GPU Phase-0 program, P0-1..P0-5) + **#172** (gate-nits). Before that: v1.73.0->v1.74.4
(#584/#585/#131-F3/#164/#166/#591); v1.70.0->v1.72.1 (#152/#127/#90b/#153/#154/#158/#159/#580/#581); the
v1.68.1 CEO WSL-dogfood 3-PR drain (#562/#563/#564 -> v1.69.0/.1/.2); campaign #142's 4-PR queue
(#554-557 -> v1.67.1-v1.68.2) -- all clean.

## SHIPPED — live on PyPI up to **v1.98.3** (v1.93.10-v1.98.3 detail in CURRENT STATE above;
v1.76.10-v1.83.0 and v1.91.0-v1.93.9 not yet individually backfilled into this section -- see
CHANGELOG.md for the authoritative per-version detail in those gaps)

**v1.84.0-v1.91.0 window (2026-07-20, merged, on PyPI/publishing) -- the CEO `/goal` #232 9-point
campaign, full per-point receipts in the header above:** #678 `tg calibrate --json` structured
`skipped_no_cuda_build` signal, CEO#9 (v1.84.0) | #679 `tg agent` best-effort primary under deadline
truncation + a structural `confidence<=0.55` cap, CEO#1 (v1.85.0) | #680 bidirectional-oracle
completeness exit-code gate + `callers` likely-first parity, CEO#4 (v1.86.0) | #681 `EvidenceReceipt` ->
`review-bundle --receipt` -> `verify --against` PR-head + `--min-receipts`/`--expect-key` policy
enforcement, CEO#8 (v1.87.0) | #682 `tg prepare` one-shot edit-readiness CUJ, CEO#5 (v1.88.0) | #683 AST
empty-result remediation + resolve-only ruleset aliases + honest sg-absent error, CEO#6 (v1.89.0) | #684
`suggested_scope`/`workspace_root_detected` proactive mega-repo auto-narrow (advisory), CEO#2 (v1.90.0) |
#687 `tg install-dense` one-shot packaged dense-embedding install, CEO#7, bundled with #686 pip-vs-native
cold-search doc-honesty (CEO#3, $0) + #685 calibrate-stdout-contract/de-flake test nit, all publishing
together as v1.91.0. Two headline fixes (GPU-calibrate structured skip, gap#2 best-effort primary)
BINARY-VERIFIED via a clean-room `uvx --from tensor-grep@1.87.0` dogfood. #677 (CI-audit
transient-503 hardening, no-release) rode between this campaign and the prior #676 backlog reconcile.

**v1.81.6-v1.81.15 window (2026-07-17/18, merged, on PyPI) -- the senior-review + Rust-dogfood campaign,
full receipts in the header above:** #655 public-shim cold-start partial win (v1.81.6) | #656 stderr
deadline-partial note (v1.81.7) | #657 deps-slim, ~31-55 MiB lighter (v1.81.8) | #658 C1/C2
deadline-honesty re-fix across 7 sites (v1.81.9) | #659 C4 symlink-safe atomic write for evidence/
review-bundle (v1.81.10) | #660 C3 MCP `tg_query` fan-out cap + shared deadline (v1.81.11) | #661 B9
`--max-files` now bounds `edit-plan` `suggested_edits` (v1.81.12) | #662 dead-code cleanup, 255 LOC/14
symbols, non-releasing chore, swept into v1.81.13 | #663 B13 LSP position-encoding negotiation + a
utf-8 column-conversion fix (v1.81.13) | #664 `defs`/symbol commands FILE-path `NotADirectoryError` fix
(v1.81.14) | #666 broader B9 flag-lie across context-render/blast-radius-plan/blast-radius-render
(v1.81.15) | #665 uniform atomic-writer symlink hardening via a shared `_index_lock` primitive (merged,
publishing as v1.81.16).

**v1.75.0-v1.75.4 window (2026-07-14, merged, on PyPI) -- GPU Phase-0 program #171 + gate-nits #172
complete:** #593 `tg orient`/`tg agent` broaden `suggested_ignore` to whole vendor/skill trees, M1+M2
(v1.75.0) | #594 GPU Phase-0 P0-1 WSL probe path-domain bridging + `cargo check --features cuda`
anti-bit-rot CI gate (v1.75.1) | #595 GPU Phase-0 P0-2/P0-3 doctor probe failure-taxonomy + honest
device-id validation (v1.75.2) | #596 GPU Phase-0 P0-4/P0-5 calibrated remediation message + loud
nvidia->cpu installer downgrade (v1.75.3) | #597 GPU Phase-0 gate-nits: doctor-probe precision + native
error-kind taxonomy, 5 nits incl. the `cfg(any(cuda,test))` classifier fix (v1.75.4). **Scope stays
CPU-default-honest** -- this hardens the gated-OFF GPU code path's correctness/observability; it does
not promote GPU or prove a speed crossover (full reframe: CEO-FACING GPU below).

**v1.73.0-v1.74.4 window (2026-07-14, merged, on PyPI):** #584 `tg edit-plan` top-level confidence +
ask_user_before_editing (v1.73.0) · #585 `--deadline` on source/docs-coverage/blast-radius-plan (v1.73.0) ·
#131-F3 fail-closed GpuSearchParams flag completeness (v1.74.1 — later dogfood-proved CLI-dead-code; the
rust GPU path is unreachable from `tg --gpu-device-ids`, which the Python Pipeline owns; #131/#165 closed) ·
#164 embed mermaid in JSON under `--json --mermaid` (v1.74.2) · #166 clean error + exit 2 for `--gpu-device-ids`
without a GPU backend (v1.74.3) · #164 orient deweight `.claude` tool-config + `suggested_ignore` (v1.74.4,
real-corpus validated). v1.74.0 (prior wave, CEO dogfood target).

**v1.71.3-v1.72.1 window (2026-07-13, merged, on PyPI):** #159/#577 `tg lsp` fail-closed on the missing `ast` extra (v1.71.3) · #580 `tg edit-plan` structured top-level `validation_plan`, parity with `tg agent` (v1.72.0) · #581 accept `--deadline`/`--no-deadline` on agent/edit-plan/context/context-render/map/orient + `--deadline` on defs (v1.72.1, dogfood-verified on the wheel: all 7 accept it, orient stays exit-0) · **#582** (`test(cli):`, merged, no release) closes #581's Opus-gate coverage gaps, full CI matrix green (`6cb53a4`). Docs-only, no release: #578 (4-skill WSL-artifact corrections) + #579 (prior backlog refresh).

**v1.70.0-v1.71.2 window (2026-07-13, merged, on PyPI):** #152/#568 sys.path.insert imports resolution — 2 HIGH (v1.70.0) · #127/#570 non-git `.gitignore` (v1.70.1) · #90b/#571 `tg doctor` ast-grep exit-0 honesty (v1.70.2) · #153/#573 `tg codemap` default deadline (v1.71.0) · #154/#574 unscoped/multi-root fast-refuse (v1.71.1) · #158/#576 `tg scan` marked-root workspace refuse (v1.71.2) · #572 skills + BACKLOG docs refresh (`docs:`) · #575 **CLOSED** (CodeQL py/redos suppression — non-functional inline comment; the API dismissal is the real fix).

**v1.59–v1.66.1 window (merged, on PyPI):** #541 index capability-validator · #542 AstBackend tree-sitter query-API repair · #543 warm-daemon default-ON flip (#94 latency lever) · #544 `--index` front-door routing · #545 `--rank` chunk cap · #2/#546 atomic + cross-process-locked index write · #547 backlog reconcile · #63/#548 iterative Go AST walk (no RecursionError) + Python `in_annotation` leak + registry-dispatch governance test · #92/#549 `tg classify --stdin/--text` · #550 ast-grep fail-closed · #551 wedged-python help-probe deflake · #552 launcher import-defer perf · #124-P2/#553 Ed25519 evidence-signing (v1.67.0) · #558 release-blocker hotfix · #554-557 campaign-100 (v1.67.1→v1.68.2, incl. #108 daemon Tier-2 -> v1.68.0, #126 apply_policy fail-open -> v1.68.1, #121 --count-matches -> v1.68.2) · #559 backlog-reconcile (docs) · #560 AGENTS.md whole-repo ruff-scope hardening (docs) · #561 backlog-refresh v1.68.1->v1.68.2 (docs) · **#562 codemap --ignore/--deadline (v1.69.0)** · **#563 nested-import recall (v1.69.1)** · **#564 suggested_scope-on-tie + normpath ..-confinement (v1.69.2)** · **#566 importers outside-root honest signal (v1.69.3, dogfood-found on flask)** · #565/#567 backlog refreshes (docs) · **#130b/#568 sys.path.insert import resolution (v1.70.0)**. Older detail below is HISTORICAL.

Prior batch: #499→v1.58.5 (tg_repo_map 512→2000) · #500→v1.58.6 (#110 write-path symlink TOCTOU) ·
#503→v1.58.7 + #505→v1.58.8 (two flaky-test root fixes) · #501→v1.58.9 (multi-pattern `-e`/`-f`) ·
#502→v1.58.10 (#49 MCP stdio byte-framing+DoS) · **#508→v1.58.11 releasing** (**H3/H4** checkpoint
arbitrary-read + disk-DoS — first codex-audit security fix live). Earlier: v1.58.0-v1.58.4 (daemon
Tier-1, native DoS, blast_radius+GPU-honesty, dual-help, ReDoS fail-closed).

---

## CODEX EXTERNAL AUDIT — HIGH WAVE COMPLETE (#123 [done])
All 5 HIGH verified still-real + fixed + adversarial-Opus-gated + PR'd (H1→#511, H2→#509, H3+H4→#508,
H5→#512, P1→#510). **The gate caught 3 real defects that would've shipped** (H5 POSIX no-op, H1
smart_case 5th silent-wrong, H2 defanged test).

## CEO DIRECTIVE 2026-07-10 (#99 [done]) — after the codex audit
**Do NOT build the SaaS.** Build tg features gotcontext.ai can wire into + focus on the tool
**WORKING** + optimally **PERFORMING**. Workstreams: (A) correctness=audit bugs; (B) perf=#94 + MED
perf; (C) wire-able=EvidenceReceipt (#124). gotcontext stays the CEO's product; we hand it clean
signed consumable tg outputs.

---

## CURRENT LIVE BACKLOG (reconciled 2026-07-13, task #162 — cross-checked against `git log` + live code, not just the ledger)

**Reconciled this pass (already shipped or resolved -> dropped from the active queue below; one-line receipts):**
- **#543** warm session-daemon default-ON flip + version-skew guard (#94) -> shipped `45000f4`, v1.65.0.
- **#544** route `--index` to the Rust capability validator (#138/#140) -> shipped `eaaaf0a`, v1.65.0.
- **#545** cap the plain-`--rank` corpus rechunk (#128d/MED-1) -> shipped `f43b7c0`, v1.65.1.
- **#2** index atomic+locked `.tg_index` write (audit A4) -> shipped `aa57254`/#546, v1.65.4.
- **#63** lang-graph crash/leak tail (Python `in_annotation` leak, Go unbounded recursion, registry-
  dispatch governance test) -> shipped `0fa47d6`/#548, v1.65.5.
- **#92** `tg classify --stdin`/`--text` literal mode -> shipped `7f11bc0`/#549, v1.65.6.
- **#130b** `sys.path.insert`/`append` import-awareness (imports/importers) -> shipped `abd58e2`/#568
  (re-tagged **#152** in later ledger entries, same fix), v1.70.0.
- **#124-P2** EvidenceReceipt signing (shipped as Ed25519, not HMAC as originally scoped — same intent:
  `tg evidence verify`/`keygen`/`pubkey`) -> shipped `5e046ed`/#553, v1.66.1.
- **#124-Gap1/Gap2** checkpoint undo persistence -> both confirmed live in code: `undo_argv`/
  `undo_command` are computed via `_undo_argv` (`checkpoint_store.py:264,871-872`) and returned on
  checkpoint create; the manifest `rollback` block is persisted in `evidence_receipt.py:651-666` and
  `apply_policy.py:988` payloads. Neither is in-memory-only anymore — no single PR to cite, closed
  incrementally across the checkpoint/evidence work.
- **#108** daemon Tier-2 (orient/agent capsules via the warm daemon) -> shipped `47174b4`/#555, v1.68.0.
- **#126** apply_policy fail-open edge (canonicalize exec parent) -> shipped `d8cf53c`/#556, v1.68.1.
- **#121** native `--count-matches` no-rg degrade -> shipped `87515df`/#557, v1.68.2.
- **#127** index-build `.gitignore` non-git-dir no-op -> shipped `2c07e0a`/#570, v1.70.1.
- **F3** GPU fail-closed capability matrix (`--gpu-device-ids` combined with ast/nlp/count/
  fixed-strings/context/line-regexp/word-regexp/LTL) -> confirmed shipped across a "round-4" audit
  pass, `pipeline.py:203-293` (each combo fails loud via `_raise_explicit_gpu_configuration_error`
  instead of silently dropping the flag). The `-o`/`--max-filesize`/`--color`/`--no-ignore-vcs` flags
  named in the original finding are output/filter concerns that never independently select a backend,
  so they were never a live instance of this gap.
- **Dead-code (partial):** `semantic_index.py` already carries the honesty docstring asked for
  (`semantic_index.py:1`, "kept SEPARATE from the Rust TGI v3 `.tg_index`"). `sidecar.py::_classify_lines`
  (the thin unused wrapper around `_classify_lines_with_metadata`) is now **DELETED** (2026-08-01
  backlog campaign, PR-D, `chore:`) — re-verified zero callers via `tg callers`/`tg refs`/tracked-file
  grep before removal, positive control 4 callers/3 files on the sibling. `rust_core/src/backend_cpu.rs::replace_in_place`
  is **RETAINED, NOT DELETED** (Task 5 Rust half, 2026-08-02 backlog closeout, branch
  `fix/rust-replace-in-place-hardening`): the in-repo zero-caller census was correct (still zero
  Rust callers) but does not authorize deletion of a `pub fn` on a public struct in a public
  `rlib` module — external/FFI consumers this repo cannot see may depend on it. Hardened instead:
  a compile-time public-signature guard, directory-walk errors and per-child literal/regex
  replace errors now propagate as `Err(...)` with path/operation context (previously discarded via
  `let _ = ...`), and 3 new `#[cfg(test)]`-gated fault-injection unit tests inside `backend_cpu.rs`
  prove each arm independently. See
  `docs/investigations/2026-08-02-replace-in-place-surface.md`.
- **#171** GPU Phase-0 program (de-risking toward a possible Phase-1 `cuda-check` CI gate) -> SHIPPED:
  P0-1 WSL probe path-domain bridging + `cargo check --features cuda` anti-bit-rot CI gate (`7f8de84`/
  #594, v1.75.1) | P0-2/P0-3 doctor probe failure-taxonomy + honest device-id validation (`7350d77`/
  #595, v1.75.2) | P0-4/P0-5 calibrated remediation message + loud nvidia->cpu installer downgrade
  (`a4b3c05`/#596, v1.75.3). Phase 0 is now DONE; Phase 1 (flipping
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`) is a reversible release-config decision, not a rebuild --
  see the reframed CEO-FACING GPU entry below.
- **#172** GPU Phase-0 gate-nits (doctor-probe precision + native error-kind taxonomy) -> shipped
  `3fd3af7`/#597, v1.75.4. 5 nits incl. a decisive one: `classify_gpu_route_failure` and its 3 tests
  were `#[cfg(feature = "cuda")]`-gated, so a default `cargo test` (no `--features cuda`) silently never
  ran them.
- **#173** this BACKLOG reconcile (docs-only, no release) -- CURRENT STATE/SHIPPING/SHIPPED refreshed to
  v1.75.4 + the CEO-FACING GPU section reframed for honesty (council must-fix MF-3, see below).

**Verify-flagged (not on the live task-store queue; also not confirmed shipped in this pass — flagged
for the next audit rather than re-opened as active work):**
- **#86** T7->T8 late-rerank (real-model latency receipt + golden-set ship/no-ship decision). T0-T6
  (foundation/ONNX encoder/`--semantic` wiring, `#471`-`#474`) shipped v1.51-v1.54; `#531` hardened the
  wall-clock deadline (audit A3, v1.63.2). No T7/T8-labeled commit found in `git log --oneline --all`;
  reads as shelved (`TG_LATE_RERANK` stays experimental/opt-in, `reranker.py:45`) rather than an open
  gap, but this pass could not confirm that either way.
- **#128c** session-daemon worker-semaphore (`TG_DAEMON_MAX_WORKERS`) — no matching symbol anywhere in
  `src/` (`session_daemon.py` has no semaphore/max-workers guard). Genuinely looks unbuilt; not on the
  live queue, so not re-added as active work, but it is the one item this pass could not verify as
  either shipped or intentionally dropped.

### Ready to build (no mandatory-gate blocker)

**PREMISE RECONCILE 2026-08-05 -- 4 of 5 checked items were ALREADY SHIPPED.** Verified against
`origin/main` before any work, per verify-plan-against-code Step 0. Same pattern the board has hit
before (one pass found 9 of 24 already done): a plan written against a fixed defect has perfectly
resolving citations, so anchor-checking cannot catch it -- only reproducing the defect can.

| item | claim | measured | disposition |
|---|---|---|---|
| **#58** | `tg route-test` is hidden, promote to public | registered at `main.py:10597` via `@app.command(name="route-test")`; the live app reports it in the public command list and it is NOT in the hidden set | **CLOSE -- already shipped** |
| **#865** | `--ltl` missing from rust_core, would clap-reject | present in `SEARCH_PYTHON_PASSTHROUGH_FLAGS` at `rust_core/src/main.rs:322` with an explaining comment at `:319` AND a parity test `search_format_python_passthrough_args_routes_ltl_flag_to_python` at `:4484`. The item's stated evidence ("rust_core has zero matches") is simply false -- 6 matches | **CLOSE -- already shipped** |
| **#858** | codemap hand-rolls `_atomic_write_text`, skipping the symlink refusal | the function survives at `codemap.py:799` but AST-walked its body is 4 statements calling only `atomic_write_bytes`/`encode`/`mkdir`, with ZERO residual write primitives (`os.replace`, `write_text`, `mkstemp`, `NamedTemporaryFile`). All three call sites (`:1242`, `:1300`, `:1307`) route through it | **CLOSE -- routed; the surviving name is a thin wrapper, not a hand-rolled writer** |
| **#859** | no AST ratchet over `replace_with_retry`/`os.replace` publish sites | `tests/unit/test_cli_atomic_writer_ratchet.py` exists with 36 tests | **CLOSE -- shipped** |
| **#864** | CWE-88: no `--` sentinel before a relative `$file` in `apply_policy` | **ALREADY FIXED -- and my first check got this WRONG.** I counted `"--"` sentinels (zero) and called the vector open. The guard is a DIFFERENT mechanism: `_policy_file_arg` (`apply_policy.py:506`) returns `f"./{relative}"` when the name starts with `-`, which makes it unambiguously a path for every argv reader on both platforms. Measured: `-cevil.ini` -> `'./-cevil.ini'`, control `normal.py` -> `'normal.py'` unchanged. The comment at `:497-505` also states the `--` omission in `_run_policy_command` is DELIBERATE -- it concerns OPERATOR-authored tokens, which that function never touches | **CLOSE -- shipped 2026-08-01 (campaign PR-D)** |
| **#862** | add a `--` sentinel before the `agent_capsule` GPU `evidence_path` positional | **ALREADY FIXED.** `agent_capsule.py` appends `"--"` immediately before the positional (`evidence_command.append("--")` then `.append(evidence_path)`, AST-verified in that order inside `_agent_gpu_evidence`), plus a second sentinel at `:1651`. The comment beside it records that the CONDITIONAL form (emit `--` only when the path starts with `-`) was considered and REJECTED because it "leaves the silent case exposed" | **CLOSE -- shipped** |

**#864 -- and the correction is the finding.** My premise check searched for ONE implementation of
a fix (a `--` sentinel), found none, and reported the vector open. The guard exists as a `./`
prefix instead. That is the SAME defect as the backlog item I was auditing: looking for a
particular fix rather than for the BEHAVIOUR. A guard can be absent, or it can be present in a
shape you did not think to grep for, and a zero cannot tell those apart.

So the sweep is **6 of 6 already shipped** -- every queued item checked, including a second CWE-88 entry (#862) whose sentinel is not only present but UNCONDITIONAL by explicit design. Every one of the five was refuted only by
executing or AST-walking the real code; not one fell to reading the item and grepping for its
stated symptom.

- **#858** (2026-07-29 audit S1) route `tg codemap` writes through `_index_lock.atomic_write_bytes`
  — retire hand-rolled `codemap._atomic_write_text` (`codemap.py:801-812`). Explicitly deferred out of
  #665/#211 as "doc-generation". Bidirectional probe 2026-07-29: baseline refuses symlink dest;
  `_atomic_write_text` replaces the link entry (target content intact — not RCE). TDD pin the refusal;
  mandatory Opus security gate (installer/write surface). Spec §3 S1.
- **#859** (audit S2) AST ratchet: every `cli/` `replace_with_retry`/`os.replace` publish site routes
  through `atomic_write_bytes` (Form-1: must report non-zero on pre-fix `codemap.py`). Closes the
  "enumeration without ratchet" hole that made #858 invisible. Ship with or immediately after #858.
- **#860** (audit D2) docs reconcile: stamp BACKLOG/CURRENT STATE + `_completeness_caveat_lines`
  docstring (`main.py:11456-11461`) to live disclosure wiring / tip **v1.101.18**. Non-releasing
  `docs:` PR. Pin SOURCE behavior beside any claim retired.
- **#861** (audit D1 residual) — **CLOSE as product gap (2026-07-31 Wave-2)**: inventory
  leading + mermaid visible node + codemap leading `PARTIAL:` already shipped. Remaining
  shared-banner unify is cosmetic; fold stale comments into **#860**.
- **#864** (2026-07-31 audit DD-001, Wave-2 LOW) CWE-88 `--` before Python relative `$file` in
  `apply_policy` (Rust already absolute). MCP default-OFF. Optional with #862.
- **#865** (2026-07-31 audit DD-008) register `--ltl` on native `SEARCH_PYTHON_PASSTHROUGH_FLAGS`
  (bootstrap already lists it; rust_core has zero matches → clap-reject). Parity test.
- **#58** promote `tg route-test` hidden->public — **VERIFY-BEFORE-BUILD:** likely already shipped as
  #601 / v1.76.0; confirm against `origin/main` before opening a PR (AGENTS "check whether it already
  shipped").
- **#98** MCP tool consolidation (45->~10 task-shaped dispatch tools, non-breaking,
  `TG_MCP_TOOL_SURFACE=lean`) + staleness receipts (P2). Design previously recovered/verified
  (campaign #142). Note: `#554`/v1.67.1 shipped a much narrower precursor under the same tracking
  number (`tg_session_open` default `max_repo_files` 512->2000) — that is NOT this consolidation.
- **#141** native `AstBackend` vs the ast-grep wrapper — DSL divergence + `is_available` broadening
  (design-stage; needs a design pass before a TDD build).
- **#160** — **CLOSED as reconciled (2026-08-01, worktree `feat/dogfood-feature-tail-160`)**: all
  four named sub-features were ALREADY SHIPPED, execution-verified against real code + the real
  CLI (not just grepped):
  - `suggested_ignore`/orient-auto-deweight — `tg orient --help` shows `--no-auto-deweight`
    (auto-deweight ON by default); `tg orient --json` on a fixture vendor tree returns
    `suggested_ignore: ["third_party/**"]`, and `None` on a clean repo (positive + negative
    control). `tg agent --json` carries the SAME `suggested_ignore` (M2 parity,
    `agent_capsule.py::build_agent_capsule` -> `orient_capsule._suggested_ignore_from_deweighted_trees`),
    verified live.
  - complete-scan `suggested_scope` — the tie-confirmation path (not just scan-limit truncation)
    already populates `suggested_scope` (`agent_capsule.py:3216-3241`, the "Dogfood fix" comment
    there names this exact gap as already closed); covered by
    `tests/unit/test_agent_capsule_tie_suggested_scope.py` (11/11 passing in this pass).
  - dynamic-import string breadth — the module-level literal-string shapes (`__import__`,
    `import_module`, `importlib.import_module`) plus the relative-import decoy exclusion shipped
    via #504/#703 (`CHANGELOG.md` v1.93.0 entry, `repo_map.py::_python_dynamic_import_entry_for_call`).
    The **getattr** half was live too, but only as an UNLABELED emergent case of the generic
    `_string_literal_references`/`string_refs` regex pass (`getattr(mod, "Widget")` matched and was
    reported, just lumped under the generic `"string-literal"` bucket, indistinguishable from an
    unrelated string assignment) — genuinely under-specified breadth. Closed the gap this pass:
    `_classify_string_reference` now returns a dedicated `"getattr-arg"` occurrence (same
    same-line, unbalanced-parens heuristic precision as the pre-existing `"decorator-arg"` check),
    additive-only (no row-count change, only a more specific label on an already-matched row).
    TDD: `tests/unit/test_repo_map_targets.py::test_string_literal_references_classifies_getattr_arg`
    (red-arm proved by reverting `repo_map.py` and re-running — confirmed `git diff` showed zero
    `getattr-arg` occurrences before the revert-triggered failure). Not an MCP contract-version
    bump: `string_refs[].occurrence` was never a closed vocabulary (`_classify_string_reference`'s
    own docstring already called it an open "any other quoted occurrence" bucket, and no
    `CONTRACTS.md`/wire-surface governance test enumerates it), so widening it is the same kind of
    change as the pre-existing `"fstring"` value, not a new field.
  - cold-doctor daemon-autostart hint — `tg doctor --json` -> `session_daemon.autostart:
    "on-first-use (not yet warmed)"` (`main.py::_doctor_session_daemon_autostart_status`, "v1.92.1
    dogfood item 5"); covered by 4 passing tests in `test_cli_modes.py`.

  The backlog line's own wording was accurate about needing "verify-against-code first" — it
  correctly hedged rather than asserting these were open. No CORRECTION needed to any other doc.

### LOW-severity follow-ups (non-blocking)
- **#862** (audit S3) add `--` sentinel before `agent_capsule` GPU `evidence_path` positional
  (`agent_capsule.py:1711-1721`); optional twin for `wslpath` in `runtime_paths`. Defense-in-depth
  (paths are `resolve()`-absolute today). MCP-276 / CWE-88 class.
- **#863** — **CLOSED** (2026-08-01 backlog campaign, PR-B). `is_authorized` now fails CLOSED when
  `self.token` is falsy; this is a POLICY REVERSAL of the previously pinned tokenless-compat
  behavior (`tests/unit/test_session_daemon_security.py::test_tokenless_daemon_fails_closed`
  replaces the retired `test_tokenless_server_stays_backward_compatible` pin), argued on the
  merits because the sole production constructor (`run_session_daemon_server`) always generates a
  token via `secrets.token_urlsafe(32)`, so the "legacy/in-test" population the old pin protected
  is empty in production.
- **#115** / **#125** — **CLOSED** (CHANGELOG closes #115/#125a; Rust checkpoint/audit/rollback
  writes use `write_bytes_refuse_symlink`). Do not re-open from the old LOW bullets; 2026-07-31
  audit DD-007.
- **#143** — **CLOSED** (2026-08-01 reconcile; all 4 named sub-items verified fixed against the
  real tree, none reopened). `#543`'s race-test = the stale-daemon-metadata ownership guard, shipped
  as task **#143a-a** (`session_daemon.py::_remove_daemon_metadata`'s `expected_pid`/`expected_port`
  check, `248fa35`/#603) with its own TDD suite
  (`tests/unit/test_session_daemon_metadata_ownership.py`). `symbol-timeout` = the #390 "9 remaining
  warm-daemon command handlers" gap (defs/impact/refs/callers/blast_radius/file_importers/
  blast_radius_render/blast_radius_plan/context all dispatched with no default deadline), closed by
  `81b2148`/#203/#652 and refined by `#205`/#658/#669 — `session_store.py` now computes
  `deadline_monotonic = monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS` on every one of those
  branches (verified live, e.g. `session_store.py` around the defs/impact/refs/callers/blast_radius
  dispatch blocks). `lru_cache` flip = `@lru_cache(maxsize=1)` on `runtime_paths._expected_tg_version`
  (`e575075`/#604), with `cache_clear()` wired into the `tests/unit/test_runtime_paths.py` fixture.
  `#140`'s `--` sentinel = the CWE-88/MCP-276 argv-sentinel sweep confirmed **PASS** by the
  2026-07-26 enterprise-readiness scorecard (`docs/plans/2026-07-26-enterprise-readiness-scorecard.md`
  row A6: "`--` sentinel work landed via #140/#143"); `rg_passthrough.rs`'s
  `ripgrep_operand_args`/`execute_ripgrep_search` and `agent_capsule.py`'s GPU-evidence positional
  both sentinel-guard user paths today. Note: **#862** is a separate, still-open sentinel nit
  (`agent_capsule.py` GPU `evidence_path`, see above) — do not conflate the two when re-triaging.
- **#155** — **CLOSED** (2026-08-01 reconcile). Both named nits on `#152`'s sys.path.insert import
  fix were closed by the SAME commit that closed #143's `lru_cache` item, `e575075`/#604 ("fix dead
  import-provenance tag in tg importers"): the dead reverse-tag block was
  `repo_map.py::_python_module_match_details` computing a `"sys-path-insert"` provenance tag that
  `_python_module_matches_definition` (its sole caller) discarded down to a bare bool — now threaded
  through as a separate `path_provenance` field (`repo_map.py:7370-7391`, docstring cites "#155 fix:
  that tag was computed but provably unreachable... before this change"), covered by
  `tests/unit/test_file_deps.py::test_build_file_importers_finds_sys_path_insert_hacked_importer`
  (asserts `provenance == "parser-backed"` + `path_provenance == "sys-path-insert"`) and a companion
  negative test asserting `"path_provenance" not in edge` for a normal (non-hacked) edge, closing the
  payload-bloat/ordering nit in the same diff.
- **Dead-code (partial, see reconciliation note above):** `sidecar.py::_classify_lines` — **DONE**
  (2026-08-01 backlog campaign, PR-D). `rust_core/src/backend_cpu.rs::replace_in_place` — **DONE,
  RETAINED (not deleted)** (Task 5 Rust half, 2026-08-02 backlog closeout): confirmed zero in-repo
  Rust callers, but retained per the public-`rlib`-API rule and hardened (see the reconciliation
  note above and `docs/investigations/2026-08-02-replace-in-place-surface.md`). Two follow-ups
  deliberately left out of scope: `RUST-REPLACE-NONEXISTENT_PATH` (a nonexistent direct-file path
  is currently a silent `Ok(())` no-op) and `RUST-REPLACE-SYMLINK=DEMAND_GATED` (direct-leaf-symlink
  follow behavior unchanged).
- **apply_policy argv-sentinel — RETIRED, not fixed (2026-08-01 backlog campaign, PR-D).** The
  `argv = [str(resolved_path), *argv[1:]]` site at `apply_policy.py:707` (mirrored in
  `rust_core/src/main.rs`'s `Command::new(program)` construction) does NOT get a `--` separator.
  The CWE-88 argv-sentinel census is keyed on "OUR flags plus an UNTRUSTED positional appended to
  a tool WE chose" (rg/ast-grep invocations); this site is the opposite shape — an
  operator-authored COMPLETE validation command, where a blind `--` has no defined semantics and
  can break it. The path-hijack half is already closed by the repo-local shadow refusal
  immediately above (`apply_policy.py:696-706`). **Mandatory adversarial security gate (rule A3)
  found a REAL, adjacent bug while attacking this claim** — the retirement claim itself held, but
  `_policy_file_arg` (`apply_policy.py:484-505`) returned a repo-controlled relative filename
  UNMODIFIED, and a file named e.g. `-cevil.ini`, substituted via `$file`/`{file}` into a policy
  command template, parses as a FLAG rather than a path on both POSIX and Windows tokenizers
  (neither `shlex.quote` nor `subprocess.list2cmdline` escape a leading dash). Fixed in the same PR
  (TDD-first, 2 new tests in `tests/unit/test_apply_policy.py`): `_policy_file_arg` now prefixes a
  dash-leading relative path with `./`. This is a DIFFERENT fix from a `--` sentinel and does not
  reopen the retirement reasoning above — it neutralizes the one token tg's OWN code appends,
  which the retirement's "operator-authored" premise never covered.

### Historical Linux/WSL block — superseded by status index `2026-08-02.3`

- **#89** is no longer environment-blocked: the 2026-08-02 bounded WSL run reproduced the
  WSL-to-Windows path-domain failure and moved it to `READY` pending an amended TDD plan.
- **#90** is `READY`. The doctor-honesty half shipped in PR #571; the 2026-08-02 treatment/control
  disproved the earlier non-defect premise by showing a raw-path false clear versus six translated-
  path matches.
- **#109** shipped in PR #605.

### CEO-gated (full framing in CEO-FACING below)
- **#72** benchmark proof-point publish.
- **#131** GPU deep-dive audit + multi-week rebuild (conflicts with no-SaaS).

---

## CEO-FACING / strategic (the CEO's call — not auto-fired)
- **#72** benchmark proof-point publish (tokens-per-correct-answer; tg **7.5x fewer tokens than grep**
  on definition-lookup, oracle-validated). Reinforced by the dogfood + GPU "published accuracy gate"
  enterprise-gap below.
- **#77** `tg ledger` local agent context-sharing (thinktank-reviewed conditional narrow-yes; gated
  behind semantic-search shipping first).
- **GPU program -- REFRAMED 2026-07-14 (Phase-0 complete: #171 + #172; council must-fix MF-3 honesty
  gate baked into this reframe).** NVIDIA native assets are BUILT and locally correctness-proven on the
  dev box (device 0 `RTX 4070` `sm_89`, device 1 `RTX 5070` `sm_120`; see `docs/SESSION_HANDOFF.md` GPU
  dogfood notes and `docs/gpu_crossover.md`), gated OFF the public release by CI Actions var
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; the opt-in flip is
  `native-frontdoor-gpu`, `.github/workflows/ci.yml:1121`). **So Phase 1 is a reversible release-config
  flag-flip decision, not the ~24wk/2-engineer rebuild this section previously described.**
  **CRITICAL HONESTY (do not violate `docs/CONTRACTS.md:80-82`):** flipping the var publishes ASSETS
  only -- it does NOT promote GPU. GPU auto-recommendation stays `false`; no speed crossover vs
  `rg`/`tg_cpu` is proven yet (`docs/gpu_crossover.md` still records "no crossover" for the measured
  workload classes); the reviewer-gated `public-gpu-proof.yml` speed-crossover gate is UNMET (manual
  `workflow_dispatch` only, requires a `self-hosted`/`gpu`/`tensor-grep-public-gpu-proof`-labeled runner,
  and its `environment: public-gpu-proof` lets maintainers require explicit approval before it runs --
  `docs/CI_PIPELINE.md`). Assets become downloadable; the CPU path remains the default and the
  recommended engine until a self-hosted GPU rig proves a crossover -- which it may not.
  **Phase 2** = attach the dev GPU box as that self-hosted runner to actually execute
  `public-gpu-proof.yml`'s speed-crossover proof. CEO-gated: needs the physical hardware attached. **Can
  still re-open the #99 "no-SaaS" wedge the CEO closed 2026-07-10 IF pursued as a funded buildout** --
  Phase 0's de-risking narrows the ask, it does not itself resolve that strategic fork. Campaign #142
  re-homes the old **#47** finding ("GPU public-proof", an NVIDIA-flavor native build) onto this same
  fork -- one CEO decision now covers both. Cite: `cluster-4-stale-reconcile.md` (#47). Phase-0 receipts:
  **#171**/**#172** (CURRENT LIVE BACKLOG above; releases in SHIPPED above). The earlier Phase-0
  honesty/correctness fix (**F3**, the GPU fail-closed capability matrix) also already shipped (see
  SHIPPED above).
- **Enterprise gaps** (dogfood-surfaced, design-scale): **multi-root workspace primitive** (orient/
  search/blast across sibling repos, no manual fan-out) · target-selection accuracy scoreboard
  (top-k/MRR) · cross-OS managed ast-grep · LSP proof-mode (availability ≠ navigation proof).
- **Next-language expansion** (Java/C#/C++/Ruby/PHP) — **SHIPPED 2026-07-24** (CEO-approved design
  plan, v1.93.10->v1.98.1, #723-#734; full per-release receipts in CURRENT STATE above; re-homed from
  **#62**; cite `cluster-4-stale-reconcile.md`). java/c#/php/c/cpp all landed at the FOUNDATIONAL tier
  (defs + imports; regex-fallback refs/callers — `references_and_calls`/`provider_alias_calls`/
  `file_imports_symbol_from_definition`/`import_update_target`/`prime_repo_context` all `None`),
  completing the **top-10 symbol-graph milestone** alongside the existing parser-backed
  py/js/ts/rust/go. **Honesty notes:** Ruby was NOT part of this wave (the original 5-item list
  shipped java/c#/php; C was added instead, and C++ shipped as a bonus 6th language beyond the
  original ask). True cross-file caller-graph resolution for all 5 new languages stays deferred to
  BACKLOG, foundational-tier only (defs + imports, regex-fallback refs). True import->file resolution
  is a SEPARATE, narrower gap that also stays deferred: `tg imports`/`tg importers` for go/php/csharp
  (#728) — each HAS a real manifest (`go.mod`/`composer.json`/`.csproj`) but tg does not resolve
  against it yet — and `#include->file` for c/cpp (#731/#732), which is harder still since C/C++ have
  no manifest concept at all to resolve against. The Go Stage-1 pattern (registry + fail-closed
  grammar-missing + `resolution_gaps`, `3481742`/#420) was the proven template that made the marginal
  per-language cost low enough to execute this whole wave in one campaign, exactly as this entry
  predicted.
  `_provider_language_for_path` already mapped java/c/cpp/csharp/php ids for the LSP-provider layer
  before this wave; the graph layer now does too — the same drift class **#63**'s F22 governance test
  (shipped, `#548`/v1.65.5) continues to guard against future drift here. **Follow-up (2026-07-24,
  same day):** the campaign's own C `#include`-resolution reconcile disclosed a file-scope
  function-pointer-VARIABLE mis-kinding bug in `lang_c.py`; it and its `lang_cpp.py` sibling are
  both now FIXED (**#736** -> v1.98.2, **#737** -> v1.98.3 — see CURRENT STATE above), closing the
  C/C++ declarator bug class on both sides. The ACCEPTED, not-fixable `class MACRO Name` misparse
  (an inherent tree-sitter-cpp preprocessor-unaware ceiling, disclosed in #732) is unrelated and
  remains as documented above — no guard was added there because it would suppress the legitimate
  `struct Point make_point() {...}` shape too.

## References
- Cross-session resume anchor (memory): `tensor-grep-drain-resume-2026-07-09.md` (live drain/audit/dogfood/GPU state).
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md).
