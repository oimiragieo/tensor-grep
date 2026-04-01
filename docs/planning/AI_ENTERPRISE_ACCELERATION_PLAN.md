# AI Enterprise Acceleration Plan (2026-03-22)

This plan turns the latest product feedback into concrete work streams for `tensor-grep`.

## Goal

Push `tensor-grep` beyond fast search into an enterprise-grade AI edit and compliance substrate:

- token-efficient context delivery for agent loops
- inspectable and auditable automated rewrites
- faster blast-radius and dependency analysis
- opt-in security and compliance rule packs
- eventual GPU-direct data paths for extreme-scale corpora

## Constraints

The codebase is benchmark-governed and contract-heavy. That means:

1. Do not mix hot-path performance work with control-plane work.
2. Keep CLI/MCP/doc changes validator-backed.
3. Benchmark any search, routing, index, GPU, or storage-path change.
4. Preserve honest coverage metadata when a feature is heuristic.

## Current Relevant State

Already implemented:

- repo map, context pack, and prompt-ready context render
- parser-backed symbol navigation across Python, JavaScript, TypeScript, and Rust
- graph-ranked file/test relevance with provenance
- checkpointed rewrite/apply flows with validation hooks
- session-backed context render and refresh
- candidate edit targets and edit-plan seeds

This means the next work should build on strong retrieval and edit control, not restart those foundations.

## Workstream A: Token-Optimized Context Packer

### Product target

Let AI agents request a compact, high-signal context bundle instead of blindly ingesting full files.

### Proposed features

1. `--optimize-context`
   - strip comments, blank lines, and low-value boilerplate from rendered source blocks
   - preserve line maps back to original files

2. `render_profile`
   - `full`
   - `compact`
   - `llm`

3. token-budget controls
   - add deterministic source trimming by approximate token budget, not just characters

4. render diagnostics
   - bytes removed
   - comments removed
   - line-map coverage

### Why it matters

- lowers API cost
- improves signal-to-noise ratio
- makes `context-render` more directly usable in external agent systems

### Suggested slices

1. compact render mode for Python source blocks
2. line-map contract for compacted blocks
3. cross-language comment stripping using parser-backed node kinds where available

## Workstream B: Cryptographic Rewrite Audit Trail

### Product target

Make automated rewrites provable and reviewable for compliance-heavy environments.

### Proposed features

1. `--audit-manifest <path>`
   - emit a signed or digest-stamped manifest for every applied edit

2. manifest content
   - rule identity
   - edit IDs
   - before/after hashes
   - file list
   - checkpoint ID
   - validation command results
   - timestamp

3. optional repository signature integration
   - detached signatures or key-backed signing

### Why it matters

- gives compliance teams an artifact instead of trust-me automation
- fits the existing checkpoint, diff, and validation workflow already in the repo

### Suggested slices

1. unsigned deterministic manifest
2. hash chaining across edits
3. optional signing backend

## Workstream C: Blast-Radius Analysis

### Product target

Turn `defs` / `refs` / `callers` / `impact` into a stronger change-impact surface.

### Proposed features

1. `tg blast-radius --symbol ... --json`
   - return downstream callers, importers, affected tests, and ranked related files

2. graph depth controls
   - `--max-depth`
   - `--include-tests`
   - `--include-importers`

3. caller-chain rendering
   - compact tree for prompt use

### Why it matters

- helps engineers and agents estimate consequences before mutation
- directly extends the parser-backed symbol graph already in place

### Suggested slices

1. one-hop blast radius over current symbol/import graph
2. depth-limited transitive traversal
3. rendered caller tree for `context-render`

## Workstream D: Native Security and Compliance Rule Packs

### Product target

Ship `tensor-grep` with opt-in AST-aware security and policy packs.

### Proposed features

1. `tg scan --ruleset <name>`
   - example packs:
     - `crypto-safe`
     - `secret-hygiene`
     - `dangerous-subprocess`
     - `tls-safe`

2. ruleset manifest format
   - language
   - AST pattern
   - severity
   - remediation text

3. machine-readable findings
   - reuse existing JSON/MCP contract discipline

### Why it matters

- moves `tensor-grep` toward local, fast, developer-facing SAST
- complements the existing AST engine instead of competing with text grep

### Suggested slices

1. manifest format plus one built-in ruleset
2. JSON findings contract
3. MCP exposure for findings

## Workstream E: cuFile / GPUDirect Storage Track

### Product target

Eventually DMA large corpora directly from storage into GPU-visible memory for repeated-query or extreme-scale workloads.

### Guardrails

This is not a near-term default-path feature.

It should remain behind explicit feature flags until:

1. cold-start search benchmarks prove real wins
2. the storage path is measurable end-to-end
3. fallback behavior is robust on non-GDS hosts

### Suggested milestones

1. storage-path benchmark harness
2. experimental GPU-direct ingestion prototype
3. repeated-query benchmark comparison against current GPU and indexed CPU paths

## Recommended Execution Order

1. Workstream A: Token-Optimized Context Packer
2. Workstream B: Cryptographic Rewrite Audit Trail
3. Workstream C: Blast-Radius Analysis
4. Workstream D: Native Security and Compliance Rule Packs
5. Workstream E: cuFile / GPUDirect Storage

## Why This Order

- A builds directly on the new render stack and helps external agent integrations immediately.
- B leverages the existing rewrite/checkpoint/validation substrate.
- C strengthens pre-change analysis with the parser-backed graph already in place.
- D becomes stronger once blast-radius and render contracts are richer.
- E is the most infrastructure-sensitive and benchmark-heavy track, so it should not block near-term product gains.

## Acceptance Standard

Every workstream should land only when it has:

1. failing tests first
2. updated CLI/MCP/docs contracts
3. required local validation
4. benchmarks where performance-sensitive

