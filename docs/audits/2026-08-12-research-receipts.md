# Research receipts — 2026-08-12

Method: Exa REST `POST /search` (17 queries: 6 research-paper category 2025-01-01..2026-08-12, 11 web),
`exa_ok=true`, run 2026-08-12; raw JSON retained at
`C:\Users\oimir\AppData\Local\Temp\opencode\exa_receipts_20260812.json` (not a repo artifact).
All 10 cited arXiv abs pages re-verified HTTP 200 on 2026-08-12; arXiv dates derived from ID (YYMM).
Uncertainty is flagged inline. Honest nulls are stated per row. No fabricated sources.
Wishlist gap: Semantic Scholar/arXiv API (citation counts, venue data) not installed; Exa REST sufficed.
Research-only. **Do not flip** `docs/TASK_BOARD.md` from this file alone (A71): dispositions here are
PROPOSED; a board transition needs the docs PR that carries this measurement in-body.

## Part A — frontier

### A1 — papers (2025–2026)

1. **Notarized Agents: Receiver-Attested Confidential Receipts for AI Agent Actions** — arXiv:2606.04193 [1], Jun 2026.
   Self-logged agent traces are forgeable; proposes receiver-attested receipts. **CONFIRMS** — closest prior art to tg's
   escrowed CI-held-key receipts; generic agent actions, NOT repo-edit-specific → whitespace remains, but watch this line.
2. **From Agent Traces to Trust: A Survey of Evidence Tracing and Execution Provenance in LLM Agents** — arXiv:2606.04990 [2],
   Jun 2026. Field survey: execution provenance is now a named research area. **CONFIRMS** (thesis is on-trend, not fringe).
3. **Verifiability-First Agents: Provable Observability and Lightweight Audit Agents** — arXiv:2512.17259 [3], Dec 2025.
   Argues auditability must be structural, not post-hoc. **CONFIRMS**.
4. **Measuring the Permission Gate: A Stress-Test of Claude Code's Auto Mode** — arXiv:2604.04978 [4], Apr 2026. Reports the
   deployed classifier gate at ~17% false-negative under stress (as reported in the abstract; not re-derived). **CONFIRMS**
   deterministic edit-control over classifier gating.
5. **SABER: Benchmarking Operational Safety of LLM Coding Agents in Stateful Project Workspaces** — arXiv:2606.01317 [5],
   Jun 2026. Safety measured in stateful workspaces, not single prompts. **CONFIRMS** (a benchmark tg receipts could target).
6. **Toward Semantically-Seeded, Graph-Propagated Impact Analysis (vision)** — arXiv:2606.18855 [6], Jun 2026. Change-impact
   needs semantic seeds + graph propagation. **CONFIRMS blast-radius; EXTENDS** — argues impact spans non-code artifacts
   (requirements/configs), which tg's code-only graph does not cover.
7. **Safer Builders, Risky Maintainers: Breaking Changes in Human vs Agentic PRs** — arXiv:2603.27524 [7], Mar 2026. Agentic
   maintenance PRs carry elevated breaking-change risk. **CONFIRMS** demand for enforced blast-radius on agent edits.
8. **Trust-Calibrated Code Review: Review Workflows for LLM-Generated Multi-File Changes** — arXiv:2606.01969 [8], Jun 2026.
   Human review of large multi-file LLM changes is the bottleneck. **CONFIRMS** — receipts/claim-checking cut review cost.

Context note: the retrieval side is visibly commoditizing — FastContext (arXiv:2606.14066 [9]), ContextBench
(arXiv:2602.05892 [10]), Code Isn't Memory (arXiv:2606.22417 [11]) all treat repo retrieval/indexing as a solved-enough
harness component. Supports "moat ≠ faster grep". No paper found shipping head-bound signed receipts for CODE EDITS
specifically (honest null; [1] is the nearest neighbor).

### A2 — industry (2025–2026)

1. Cursor shipped **agent sandboxing** on macOS/Linux/Windows (blog 2026-02-18) [12]; Cursor 2.0 auto-sandbox Oct 2025.
2. Cursor's verification stack described as CI + security review + risk scoring + behavioral artifacts + review agents
   (Arize writeup, 2026-07-20; secondary source) [13]. Edit verification is becoming productized, but proprietary/in-platform.
3. GitHub **Copilot coding agent auto-validates security/quality** of its own PRs (changelog 2025-10-28) [14] — vendor-side
   edit verification shipping at scale; evidence lives in the PR, not a portable receipt.
4. Anthropic **Auto Mode** for Claude Code (blog 2026-03-24; GA 2026-07-10 per the same page) [15] — classifier-gated
   permissions; pairs with [4]'s measured false-negative rate.
5. **MCP deferred/lazy tool loading became real**: user demand (issue #11364, 2025-11-10) [16] → Claude Code tool-search ships,
   tools no longer load at session start (third-party writeups 2026-05/06) [17][18]; `/doctor` warns above a 25k-token MCP
   context threshold (issue #16234, 2026-01-04) [19]; auto-trigger threshold bugs filed (issue #19890, 2026-01-21) [20].
6. Sourcegraph **Amp** GA'd 2025-05-15 [21], live as a frontier agent product 2026-08-12 [22] — agent harnesses, not code
   search, are Sourcegraph's forward bet.

## Part B — demand rows

### #255 — many-pattern dedup / Aho-Corasick
Security scanners genuinely run 100s of fixed anchors through Aho-Corasick: TruffleHog documents AC across ~800 detectors
(2025-01-23) [23][24]; 2026 comparisons keep rule counts in the hundreds [25]. But that demand is INTERNAL to scanner
engines — no external signal found that agent workflows want a general many-fixed-pattern CLI lane, and tg's #255 dedup
over-count bug remains guarded. **LEAVE — workload class confirmed real but scanner-internal; reopen trigger = tg `scan`
ruleset growth past ~100 anchors or a named user with a 100+-pattern workload.**

### DD-006 — daemon DoS
Local-socket daemon abuse is an actively CVE'd class in 2025–2026: Avahi simple-protocol server ignored its client limit →
local DoS, CVE-2025-59529 (Nov 2025) [26]; Lima guest-agent socket privesc CVE-2026-53657 (Jul 2026) [27]; Docker Desktop
unauthenticated local API socket CVE-2025-9074 (Sep 2025) [28]. Honest null: no 2025–2026 advisories found for
rust-analyzer, watchman, or LSP servers treating local-socket DoS as a threat class. **LEAVE — the class is real but tg's
shipped bounded-pre-auth-read + socket-timeout posture already matches the mitigation pattern; no dev-tool-daemon-specific
demand signal.**

### AST-DSL-PARITY — native-speed metavariables
ast-grep ecosystem is growing: ~13k GitHub stars claimed by Codemod's founder (2026-03-16; secondary) [29], a JS/TS
transform runtime built on it (jssg, Oct 2025) [30], an official ast-grep MCP server (~447 stars, accessed 2026-08-12) [31],
practitioner coverage through 2026 [32][33]. Honest null: zero evidence anyone is blocked on metavariable performance —
the discourse is about capability/adoption, not speed. **LEAVE — growth confirms wrapping ast-grep was right; no consumer
demands native-speed metavars; reopen trigger = a concrete perf-blocked consumer (per #141's existing gate).**

### MCP-LEAN-DEFAULT — lean tool surfaces
Strong convergence: Claude Code shipped tool-search/deferred loading so MCP tools no longer front-load context [16][17][18],
warns at 25k MCP tokens [19], and threshold-tuning bugs show active investment [20]; AWS's MCP tool-design guidance names
definition bloat as failure mode #1 (2026-07-09) [34]. Honest null: no primary receipts this pass on Cursor/OpenCode/Copilot
deferred-loading specifics. **PROPOSED_REOPEN — lean-by-default is now the industry direction; a lean tg MCP default (fewer/
smaller schemas, deferred detail) is cheap alignment, slightly de-urgented by client-side deferral. Still sequenced after
Task 2C per the existing MCP-SURFACE ladder.**

### CONTINUOUS-REFRESH — warm persistent index serving
Cursor treats a continuously-synced codebase index as core infrastructure (secure-indexing blog, 2026-01-27) [35], and
2025–2026 produced a wave of independent warm code-index daemons for agents: zoekt-mcp (2026-02) [36], TriSeek local context
daemon (2026-03) [37], pgmcp continuous Postgres indexer (2026-03) [38], codescope watch-first symbol graph (2026-06) [39],
Code-Index-MCP (2025-05) [40], plus a wrapper reusing Cursor's own index backend (2025-10) [41]; academically [11] argues
the same. **PROPOSED_REOPEN (scoping only) — warm serving is trending to table stakes for agent search; tg's banked
"big-refactor" note stands, so reopen for a design/scoping pass, not a build.**

### RUST-REPLACE-SYMLINK — symlink policy for in-place replace
Exactly this class earned fresh 2026 CVEs in peer tools: GNU sed `-i --follow-symlinks` TOCTOU CVE-2026-5958, fixed in sed
4.10 (oss-security 2026-05-13; NVD CWE-367) [42][43]; uutils coreutils `install` unlink-then-create-without-O_EXCL TOCTOU
(GHSA-239g-2685-54x3; CVE-2026-35356/35359, Apr 2026) [44][45]; rsync path-based symlink races (advisory, <3.4.3) [46];
Capgo CLI arbitrary overwrite via symlink-follow CVE-2026-56236 (2026-06-21) [47]. Honest nulls: no CVEs found for ripgrep/
sd/fastmod; (background knowledge, not re-verified: ripgrep's `--replace` rewrites output only, so it has no in-place
surface). **PROPOSED_REOPEN — the deferred Rust `replace_in_place` symlink behavior (A49) sits in a class actively earning
CVEs in 2026; close it deliberately: no-follow-by-default or a documented boundary, plus an Event-gated swap test.**

## Sources

1. https://arxiv.org/abs/2606.04193 (verified 2026-08-12)
2. https://arxiv.org/abs/2606.04990 (verified 2026-08-12)
3. https://arxiv.org/abs/2512.17259 (verified 2026-08-12)
4. https://arxiv.org/abs/2604.04978 (verified 2026-08-12)
5. https://arxiv.org/abs/2606.01317 (verified 2026-08-12)
6. https://arxiv.org/abs/2606.18855 (verified 2026-08-12)
7. https://arxiv.org/abs/2603.27524 (verified 2026-08-12)
8. https://arxiv.org/abs/2606.01969 (verified 2026-08-12)
9. https://doi.org/10.48550/arxiv.2606.14066 (Exa hit, 2026-06-12)
10. https://arxiv.org/abs/2602.05892 (verified 2026-08-12)
11. https://arxiv.org/abs/2606.22417 (verified 2026-08-12)
12. https://cursor.com/blog/agent-sandboxing (2026-02-18)
13. https://arize.com/blog/inside-cursors-agent-factory-how-it-verifies-ai-written-code/ (2026-07-20)
14. https://github.blog/changelog/2025-10-28-copilot-coding-agent-now-automatically-validates-code-security-and-quality/ (2025-10-28)
15. https://claude.com/blog/auto-mode (2026-03-24; GA note 2026-07-10)
16. https://github.com/anthropics/claude-code/issues/11364 (2025-11-10)
17. https://wmedia.es/en/tips/claude-code-mcp-tool-search (2026-06-12)
18. https://startdebugging.net/2026/05/how-to-reduce-the-number-of-mcp-tools-claude-loads/ (2026-05-25)
19. https://github.com/anthropics/claude-code/issues/16234 (2026-01-04)
20. https://github.com/anthropics/claude-code/issues/19890 (2026-01-21)
21. https://www.linkedin.com/posts/sourcegraph_our-new-agentic-coding-tool-amp-is-now-activity-7328799940896280576-yDQB (2025-05-15)
22. https://ampcode.com/ (accessed 2026-08-12)
23. https://trufflesecurity.com/blog/under-the-hood-the-algorithmic-power-behind-trufflehog-s-secret-scanning-(part-1-of-2) (2025-01-23)
24. https://trufflesecurity.com/blog/making-trufflehog-faster-with-aho-corasick (accessed 2026-08-12)
25. https://rafter.so/blog/secrets/gitleaks-vs-trufflehog (2026-06-04)
26. https://zeropath.com/blog/avahi-simple-protocol-server-dos-cve-2025-59529 (2025-11-18)
27. https://www.sentinelone.com/vulnerability-database/cve-2026-53657/ (2026-07-10)
28. https://undercodetesting.com/the-cve-2025-9074-nightmare-your-docker-desktop-could-be-giving-attackers-root-access/ (2025-09-03; secondary)
29. https://www.linkedin.com/posts/alexbit_ast-grep-is-the-leading-code-search-and-transformation-activity-7439153701098786817-8hAT (2026-03-16; secondary)
30. https://codemod.com/blog/jssg (2025-10-13)
31. https://github.com/ast-grep/ast-grep-mcp (accessed 2026-08-12)
32. https://theparallelstack.substack.com/p/3-structural-code-search-with-ast (2026-01-28)
33. https://blog.arcbjorn.com/state-of-cli-coding-agents-2026 (2026-07-04)
34. https://aws.amazon.com/blogs/machine-learning/mcp-tool-design-practical-approaches-and-tradeoffs/ (2026-07-09)
35. https://cursor.com/blog/secure-codebase-indexing (2026-01-27)
36. https://github.com/pleme-io/zoekt-mcp (2026-02-16)
37. https://github.com/Sagart-cactus/TriSeek (2026-03-29)
38. https://github.com/vinary-tree/pgmcp (2026-03-07)
39. https://github.com/abdulmunimjemal/codescope (2026-06-01)
40. https://github.com/ViperJuice/Code-Index-MCP/ (2025-05-28)
41. https://github.com/haorwen/Cometix-Indexer (2025-10-31)
42. https://openwall.com/lists/oss-security/2026/05/13/1 (2026-05-13)
43. https://nvd.nist.gov/vuln/detail/CVE-2026-5958 (accessed 2026-08-12)
44. https://github.com/uutils/coreutils/security/advisories/GHSA-239g-2685-54x3 (accessed 2026-08-12)
45. https://nvd.nist.gov/vuln/detail/CVE-2026-35359 (accessed 2026-08-12)
46. https://github.com/RsyncProject/rsync/security/advisories/GHSA-4h9m-w5ff-j735 (accessed 2026-08-12)
47. https://notcve.org/cve/CVE-2026-56236 (2026-06-21)
