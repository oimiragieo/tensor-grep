# tensor-grep v1.7.0 Post-Release Audit Design

## Current System Context

`tensor-grep` is a Python CLI with Rust/PyO3 and native binary paths. The stable product surface includes cold text search, native CPU search, native AST search/rewrite, repeated-query acceleration, GPU/NLP paths, and MCP tools for agent integration. `v1.7.0` added MCP runtime capability discovery and embedded-safe rewrite fallback for PyPI wheel installs.

## Proposed Solution

Run a post-release operational audit before any new feature work:

1. Verify released PyPI CLI and MCP behavior.
2. Verify local `main` is synced and clean enough to audit.
3. Verify local GPU/device discovery and benchmark skip/fail/win behavior.
4. Verify edit and MCP contract tests.
5. Update docs only if fresh verification changes the accepted story.
6. If any check fails, switch to systematic debugging and fix only the concrete failure with TDD.

## Architecture Impact

No architecture changes are planned in this audit. Any future GPU improvement should be a separate native/GPU design track:

- Keep current GPU routing benchmark-governed and opt-in.
- Treat cuDF Glushkov-NFA and bit-parallel engines as research candidates, not drop-in dependencies.
- Preserve embedded-safe rewrite for PyPI/MCP users and native-required envelopes for tools that need standalone `tg`.

## Data/API/UI Impact

- Data model: none.
- API: none unless MCP capability output fails contract tests.
- UI/frontend: not applicable; this is a CLI/library project.
- Docs: update README, `docs/PAPER.md`, `docs/benchmarks.md`, or CHANGELOG only when new artifacts require it.

## Security, Privacy, Reliability

- No secrets or customer data are required.
- No production service changes.
- Reliability focus is deterministic CLI/MCP behavior and honest benchmark gating.
- GPU benchmark failures must not silently degrade into misleading CPU-only artifacts.

## Alternatives Considered

- **Ship new GPU code now:** rejected. Current research is promising but not drop-in and requires a benchmark-governed design.
- **Update marketing docs from release confidence alone:** rejected. Docs should only change from fresh artifacts.
- **Run full rewrite of CLI control plane immediately:** deferred. `docs/PAPER.md` already frames that as a larger roadmap item.

## Research Findings

- RAPIDS cuDF is actively experimenting with Glushkov-NFA regex execution in 2026, but the PR is draft and has pattern limitations.
- cuDF maintainers identify regex throughput as an open performance area, especially for longer strings and memory-bandwidth efficiency.
- BitGen and HybridSA support the direction of bit-parallel GPU regex, but adoption requires a new integration plan and parity tests.
- Community signals still reinforce `rg` as the default text-search quality baseline and AST tools as the structural comparator set.

## Open Questions

No CEO approval is needed for this audit. Approval would be needed before production deployment, paid service changes, or a large GPU engine rewrite.
