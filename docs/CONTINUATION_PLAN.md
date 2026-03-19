# tensor-grep Post-Mission Continuation Plan

## For: Next agent picking up after the native CPU/GPU/index/rewrite milestones

## Status

This document replaces the older continuation plan that assumed:

- `rg` subprocess control-plane routing was still the main text path
- GPU was still Python-sidecar-first
- harness JSON contracts, routing policy, index hardening, and workflow integration were incomplete

That is no longer the repo state.

The project now has:

- native CPU text search in Rust
- native AST search in Rust
- native AST rewrite plan/diff/apply/verify in Rust
- native trigram index build/load/query/update paths
- native GPU engine in Rust with smart routing and calibration
- harness-facing JSON and NDJSON outputs
- MCP workflow integration

The next phase is not architecture invention. It is:

1. API freeze
2. benchmark freeze
3. reliability / soak hardening
4. external harness adoption
5. governance

---

## Current State

### Validation baseline

At mission close:

- Rust tests: `145`
- Python tests: `549`
- mission validation assertions: `75/75`
- lint / type / benchmark gates: green

### Product capabilities

| Capability | Status | Notes |
|---|---|---|
| Native CPU text engine | Production | Embedded grep crates, no `rg` subprocess required for the fast path |
| Native AST search | Production | Faster than `sg` on accepted benchmark corpus |
| Native AST rewrite | Production | Plan / diff / apply / verify, deterministic edit IDs |
| Native GPU engine | Production | Rust CUDA path, calibrated routing |
| Index subsystem | Production | Binary persistence, invalidation, compression, incremental update path |
| Harness API | Production v1 | Unified JSON envelope, documented examples, compatibility tests |
| Workflow integration | Production first cut | MCP tools, NDJSON, batch rewrite API |

### Key invariants that must remain true

1. Native hot paths stay native:
   - text search
   - AST search
   - AST rewrite
   - index query
   - GPU search
2. All machine-readable outputs stay single-document unless explicitly NDJSON.
3. JSON envelope stays coherent:
   - `version`
   - `routing_backend`
   - `routing_reason`
   - `sidecar_used`
4. Rewrite dry-run never mutates files.
5. Rewrite apply validates overlaps before any write reaches disk.
6. Verification uses byte-level exact text matching, not heuristic AST membership.
7. Index files remain self-identifying and versioned (`TGI\\x00` + version byte).
8. Auto-routing stays benchmark-governed, not guess-driven.

### Accepted benchmark story

Treat the current benchmark line as the accepted baseline until superseded by a measured win.

Key claims already established:

- cold generic text search is near-`rg`
- large-file CPU path beats `rg`
- native GPU path wins materially only above measured crossover points
- AST search beats `sg` on the accepted benchmark corpus
- rewrite plan/apply is at or near practical parity for harness use
- warm indexed search beats cold scans on repeated-query workloads

Do not reopen already-closed architecture work unless a benchmark regression forces it.

---

## What remains

### Workstream 1: API Freeze

Goal:
- treat the harness-facing contracts as public API and freeze them intentionally

Why:
- the project is now usable by agents and external harnesses
- undocumented or drifting JSON/NDJSON/MCP contracts will become the next source of instability

Tasks:

1. Version all machine-facing contracts explicitly:
   - search JSON
   - rewrite plan JSON
   - apply+verify JSON
   - calibrate JSON
   - NDJSON row format
   - MCP request/response payloads

2. Add compatibility policy docs:
   - additive field changes
   - breaking field changes
   - version bump rules

3. Add or extend compatibility tests:
   - parse golden artifacts
   - validate required fields and types
   - reject accidental contract drift

4. Publish example artifacts for the current accepted line:
   - normal search
   - indexed search
   - rewrite plan
   - apply+verify
   - GPU search
   - NDJSON stream sample
   - MCP tool response sample

Acceptance:
- all public output shapes are documented
- all public output shapes have locked compatibility tests
- no undocumented machine-facing payload remains

---

### Workstream 2: Benchmark Freeze

Goal:
- freeze the benchmark matrix that supports product claims

Why:
- the repo has enough power now that benchmark drift is the main risk
- routing and marketing claims must stay tied to reproducible measurements

Tasks:

1. Publish the benchmark matrix in docs:
   - cold text search
   - large-file CPU
   - large-file GPU
   - multi-GPU
   - repeated-query indexed
   - AST search
   - rewrite plan / diff / apply / verify
   - harness loop

2. Freeze artifacts / baseline naming conventions for:
   - CPU search
   - GPU search
   - AST search
   - rewrite
   - harness loop
   - index scaling

3. Add schema tests for benchmark artifacts:
   - explicit backend labels
   - environment capture
   - threshold metadata
   - artifact version / suite naming

4. Add benchmark publication guidance:
   - what counts as accepted
   - what must be re-run before updating docs
   - how to reject noisy or misleading wins

Acceptance:
- benchmark artifacts are reproducible and machine-comparable
- the benchmark matrix in docs matches the actual scripts
- no accepted performance claim is undocumented

---

### Workstream 3: Reliability and Soak Hardening

Goal:
- move from “passes tests” to “survives repeated real usage”

Why:
- the next failures are more likely to be long-run or degraded-state failures than simple unit regressions

Tasks:

1. Add soak scenarios:
   - repeated index build / update / query cycles
   - repeated calibrate / route / search cycles
   - repeated search -> plan -> diff -> apply -> verify cycles

2. Add fault-injection scenarios:
   - corrupt index
   - incompatible index version
   - partial GPU failure
   - CUDA unavailable at runtime
   - malformed GPU output
   - interrupted rewrite apply
   - stale-file races

3. Add mixed-repo safety scenarios:
   - BOM
   - CRLF / LF
   - non-ASCII
   - binary files
   - large-file guards

4. Define crash-recovery expectations:
   - atomic write guarantees
   - index rebuild guarantees
   - routing fallback behavior

Acceptance:
- the system recovers or fails cleanly under repeated and degraded conditions
- no silent corruption paths remain

---

### Workstream 4: External Harness Adoption

Goal:
- prove the tool works as the default search/edit substrate for real harness consumers

Why:
- internal tests are no longer enough
- the next proof point is consumption by external agent loops

Tasks:

1. Add one or two end-to-end harness integration fixtures that use only public interfaces:
   - CLI JSON / NDJSON
   - MCP tools

2. Publish a harness cookbook:
   - search flow
   - indexed search flow
   - rewrite planning flow
   - diff review flow
   - apply+verify flow
   - calibrate / routing interpretation

3. Add adoption smoke tests:
   - parse search JSON
   - parse rewrite plan JSON
   - parse combined apply+verify JSON
   - consume NDJSON streaming
   - invoke MCP rewrite/index tools

4. Add guidance for large data and large repos:
   - when to use index
   - when GPU should win
   - when GPU should not be forced

Acceptance:
- at least one real harness workflow is validated end-to-end against public APIs only
- docs are sufficient for another team or agent to consume the tool without repo spelunking

---

### Workstream 5: Governance

Goal:
- keep the wins from regressing

Why:
- once the core product exists, governance becomes the multiplier

Tasks:

1. Keep regression gates explicit for:
   - cold text search
   - large-file CPU
   - GPU crossover
   - multi-GPU
   - indexed warm-query
   - AST search
   - rewrite plan/apply
   - harness loop

2. Keep routing regression tests aligned with current routing docs and calibration logic.

3. Keep docs honest:
   - remove stale caveats as code changes
   - do not let routing docs drift from code
   - do not let examples drift from contract tests

4. Keep `AGENTS.md` current when architectural invariants change.

Acceptance:
- the repo stays benchmark-governed and contract-heavy instead of drifting back into guesswork

---

## Execution Order

Recommended order:

1. API Freeze
2. Benchmark Freeze
3. Reliability and Soak Hardening
4. External Harness Adoption
5. Governance cleanup and lock-in

This order is intentional:

- freeze the contract before more consumers rely on it
- freeze the benchmark matrix before more claims are made
- harden reliability before broader adoption
- prove harness use before calling the rollout complete

---

## Working Method

Keep using the repo rules from `AGENTS.md`:

1. failing test first
2. smallest defensible change
3. focused tests
4. full validation
5. relevant benchmark
6. reject regressions
7. update docs only after acceptance

Required validation before push for code changes:

```powershell
uv run ruff check .
uv run mypy src/tensor_grep
uv run pytest -q
```

Rust:

```powershell
cd rust_core
cargo test
```

Use the right benchmark for the changed surface. Do not claim a speedup without measured numbers on the current accepted baseline.

---

## What NOT to do

- Do not reopen already-closed architecture migrations without benchmark evidence.
- Do not reintroduce Python into the hot path.
- Do not widen auto-routing behavior without measured crossover proof.
- Do not break machine-facing contracts casually.
- Do not let benchmark docs diverge from actual scripts.
- Do not push from a dirty worktree when replay-worktree discipline is required.

---

## Progress Tracker

Use this section to update progress as work lands.

- [x] API Freeze
- [x] Benchmark Freeze
- [x] Reliability and Soak Hardening
- [x] External Harness Adoption
- [x] Governance

Current in-flight slice:

- API Freeze:
  - added committed contract artifacts for `calibrate.json`, `search.ndjson`, and `mcp_rewrite_diff.json`
  - extended `docs/harness_api.md` with Calibrate JSON, Search NDJSON, MCP Tool Responses, and Compatibility Policy
  - extended schema compatibility coverage in both Python and Rust tests so these surfaces are now locked by the repo
- Benchmark Freeze:
  - converted `docs/benchmarks.md` from a dated snapshot into a benchmark matrix plus artifact/governance reference
  - normalized benchmark artifact stamping for `run_benchmarks.py`, `run_native_cpu_benchmarks.py`, and `run_ast_workflow_benchmarks.py`
  - added benchmark artifact schema tests covering the committed benchmark JSON surface
- Reliability and Soak Hardening:
  - added repeated-mutation index coverage to prove warm index rebuild/update cycles stay correct across add/modify/delete loops
  - added repeated `tg calibrate` overwrite coverage so the persisted crossover contract stays stable across reruns
  - added GPU sidecar recovery coverage proving a malformed payload failure does not poison the next successful invocation
- External Harness Adoption:
  - added `docs/harness_cookbook.md` covering public CLI JSON, NDJSON, rewrite, diff, apply+verify, MCP, and calibrate/routing flows
  - added README links pointing harness consumers to the contract and cookbook docs instead of requiring repo spelunking
  - added CLI-only adoption smoke coverage for search -> ndjson -> rewrite plan -> diff -> apply+verify using the native binary
  - added MCP adoption smoke coverage proving `tg_index_search`, `tg_rewrite_plan`, `tg_rewrite_diff`, and `tg_rewrite_apply` round-trip against the real native binary
- Governance:
  - added README governance links for the canonical benchmark, routing, harness contract, and harness cookbook docs
  - added public-doc regression coverage locking those links and the current routing policy backend inventory/decision rules

When a workstream is completed:

1. update this file
2. update any affected docs
3. record accepted benchmark lines in `docs/PAPER.md` when appropriate
