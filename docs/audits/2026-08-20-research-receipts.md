# Research receipts -- 2026-08-20

Method: Exa REST (`POST https://api.exa.ai/search`, `POST https://api.exa.ai/contents`) run 2026-08-20,
16 queries: 12 web with `startPublishedDate=2026-08-12` (the delta window since the last sweep), 4
`category="research paper"`. Paper identities/dates were then re-derived from the **arXiv Atom API**
(`export.arxiv.org/api/query?id_list=...`), not from Exa metadata, because Exa's library rows returned
`publishedDate: null` for most papers. Repo/star/release facts come from the **GitHub REST API**, not
from a blog's restatement. Every claim below carries a URL and a date.

Wishlist gap (would have changed a check): the **Exa MCP tools were not exposed to this seat** -- only
`Bash/Read/Grep/Glob/WebSearch` were callable and no `ToolSearch` existed, so Exa was reached by raw
REST with `$EXA_API_KEY`. Functionally equivalent for search+contents; it cost the `livecrawl` and
`findSimilar` conveniences. Semantic Scholar (citation counts / venue) is still not installed, so no
paper below is weighted by citation impact -- treat all of them as **preprints, unrefereed**.

Honest nulls are stated per row. Marketing vs measured is labelled per row. Research-only: dispositions
here are PROPOSED. **Do not flip `docs/TASK_BOARD.md` from this file alone** (A71).

**NEW findings this sweep: 11.** (Items marked KNOWN restate the 2026-08-12 / 2026-08-14 banks and are
included only where this sweep changed their strength.)

---

## Q1 -- competitor moves in agentic code search / navigation CLIs and MCP servers

### 1.1 ast-grep: no new release since the last sweep; adoption number restated from the primary API [NEW, minor]
GitHub REST API, queried 2026-08-20: latest release **0.45.1, published 2026-08-07**; prior 0.45.0
2026-07-23. `stargazers_count = 15599`, `pushed_at = 2026-08-18` [1][2].
- MEASURED (GitHub API, primary). This supersedes the 2026-08-12 bank's `~13k stars claimed by
  Codemod's founder (2026-03-16, secondary)` -- +~2.6k in ~5 months, i.e. steady, not explosive.
- The 2026-08-14 bank's "Rust tree-sitter rewrite ~22% faster" is **KNOWN and unchanged** -- no release
  since. Honest null: zero 2026-08-12..20 hits for an ast-grep metavariable-performance complaint, so
  the AST-DSL-PARITY `LEAVE` from 2026-08-12 still has no reopen trigger.

### 1.2 Atlassian ships "Code Context", a multi-repo context engine for agents [NEW]
Atlassian blog, **2026-08-12** [3]: "Atlassian Code Context brings large-scale, multi-repo codebase
understanding into the Teamwork Graph so Rovo and coding agents produce better output with fewer
tokens", explicitly framing cross-repo dependencies, ownership boundaries and **downstream impacts**,
plus non-code signals (architectural decisions, product strategy, conversations).
- MARKETING (vendor launch post; no benchmark, no numbers). Value is directional.
- Reads directly onto tg's blast-radius pitch, and matches arXiv:2606.18855's "impact spans non-code
  artifacts" extension already banked 2026-08-12. A platform vendor is now selling the org-graph half
  that tg's code-only graph does not cover.

### 1.3 Cursor buys production telemetry (Firetiger) and launches a code-hosting rival [NEW]
Cursor blog **2026-08-13**: Firetiger joins Cursor -- "agents that work on software once it reaches
production... monitor rollouts, catch regressions, investigate incidents, and pass what they find back
to coding agents" [4]. TechCrunch **2026-08-18**: Cursor launches a GitHub-rival hosting platform [5].
Cursor also published an MCP-for-coding-agents guide **2026-08-12** [6].
- MARKETING (acquisition post) / secondary press. No product measurement.
- Direction: Cursor is expanding **outward** from the editor (hosting, production feedback), not
  downward into code-search primitives. Consistent with the 2026-08-12 read that Sourcegraph/Cursor
  bet on harnesses, not search.

### 1.4 Sourcegraph Cody moves enterprise-only [NEW, secondary-only]
DEV Community review, **2026-08-17**, reports Cody as now enterprise-only [7].
- SECONDARY, UNVERIFIED against a Sourcegraph primary source this pass (honest null: no
  sourcegraph.com primary confirming the tier change surfaced in the delta window). Treat as a rumour
  worth one primary check, not a fact.

### 1.5 A visible wave of "codebase map / graph for agents" launches in the delta window [NEW]
Eight days produced: Graft, "a codebase map for coding agents" (2026-08-13) [8] whose companion post is
titled *"Give Your Agent a Map of Your Codebase, Not a Grep"* (2026-08-13) [9]; `code-graph-rag` on
PyPI (2026-08-14) [10]; `repository-intelligence-engine` on npm (2026-08-13) [11]; `codegraph` MCP tools
reference (2026-08-15) [12]; `gortex`, "high-performance code-intelligence" (2026-08-20) [13];
`code-review-graph`, local-first (2026-08-20) [14].
- MEASURED only as *existence and date* (package/registry pages). No quality or perf numbers for any.
- The category tg competes in is **crowding at the map/graph layer specifically**, and the marketing
  frame ("a map, not a grep") is now other people's copy too. TriSeek / cgh / seekr, named in the
  2026-08-14 bank, produced **no new primary release evidence** in this window -- honest null.

### 1.6 DeepSeek open-sources a plugin-everything agent harness [NEW, adjacent]
The New Stack **2026-08-13** [15]; InfoQ **2026-08-20** framing it as "modular, unbundled AI agent
infrastructure" [16].
- SECONDARY press. Relevance to tg is indirect but real: an unbundled harness market is a market that
  buys a *tool*, which is tg's shape.

---

## Q2 -- code retrieval quality on CPU: anything that beats BM25 + dense RRF cheaply

### 2.1 "Better Call Grep" -- index-free lexical retrieval matches graph-based RAG [NEW, strongest item in Q2]
arXiv:2601.23254, submitted **2026-01-30**, latest revision **v3 2026-07-29** [17]. The abstract states
that *Naive GrepRAG* -- letting the LLM autonomously generate **ripgrep** commands -- "achieves
performance comparable to sophisticated graph-based baselines", motivated explicitly by the
"substantial computational overhead for index construction and maintenance" of semantic/graph RAG.
- MEASURED (authors' own empirical study; unrefereed preprint, numbers not re-derived here).
- Cuts **both ways** for tg, and that is the honest reading: it strengthens the "no-GPU, cheap,
  lexical-first" architecture tg already ships, and simultaneously weakens the marginal value of the
  *dense* half of `tg find` on the code-completion task. It does not test NL-intent search, which is
  the query class `TG_FIND_DENSE_WEIGHT` exists for.

### 2.2 Three new agentic-retrieval benchmarks land, and they redefine "relevant" [NEW]
- **CORE-Bench** (arXiv:2606.11864, v1 2026-06-10, v2 2026-07-13) [18]: >180K queries, 106K
  broader-context relevance labels; measures code understanding, issue-to-edit localization, and
  broader-context retrieval. Reports "a sharp drop from traditional code search to code retrieval in
  agentic coding settings" for representative embedding models. MEASURED.
- **SWE-Explore** (arXiv:2606.07297, 2026-06-05) [19]: 848 issues, 10 languages, 203 repos; asks for a
  **ranked list of code regions under a fixed line budget**, scored on coverage, ranking and
  *context-efficiency*, with line-level ground truth distilled from successful agent trajectories.
  MEASURED.
- **Agent Retrieval Bench** (arXiv:2607.24882, 2026-07-27) [20]: 427 samples / 25 repos / 392,000 files
  / 7.9M chunks; relevance is "what an agent needs next", not query-file similarity; tasks include
  `edit2ripple`; and it ships **50 natural no-gold cases plus 32 counterfactual wrong-repository
  controls** to score *selective abstention*. MEASURED.
- Why this matters more than any single number: all three score the thing tg's budget-fitted output and
  fail-closed refusal actually do -- ranked-under-budget, ripple/blast-radius, and **knowing when to
  return nothing**. A benchmark with wrong-repository controls is the first external harness that can
  reward a fail-closed refusal instead of punishing it.

### 2.3 CodeNib -- multi-view repo-context serving, with the first hard incremental-update numbers [NEW]
arXiv:2607.25431, **2026-07-28** [21]. Builds lexical + dense + structural views per commit and
maintains them across edits. Reported: across 100 snapshots, when outputs match an independent rebuild,
**graph and vector updates are 8.7x and 25.4x faster at the median**; on the static-navigation subset
matching normalized live-server locations (63% of 1,000 requests), the **median per-request
live/static latency ratio is 4.7x**; selected context policies preserve localization with **50-87%
fewer trajectory tokens than paired grep/read**.
- MEASURED (authors' numbers, quoted verbatim, not re-derived).
- This is the quantitative backing the 2026-08-14 "warm serving is table stakes" note lacked -- and
  note the 4.7x favours the *static* index over the live language server, i.e. it argues for tg's
  posture rather than for an LSP dependency.

### 2.4 HAKARI-Bench -- a cheap way to sit a retrieval change on a Pareto frontier [NEW, methodological]
arXiv:2606.22778, **2026-06-22** [22]. 35 benchmarks / 551 tasks / 43 languages of "Nano-sets";
compares five retrieval families (BM25, dense, sparse, late interaction, rerankers) plus efficiency
variants (dimensionality reduction, quantization, reranking) under identical conditions; across 55
models its overall ranking reproduces MTEB retrieval v2, MMTEB v2 retrieval and English BEIR (full) at
**Spearman > 0.97**. MEASURED. Not code-specific -- that is the limitation.

### 2.5 RepoNavigator -- one tool (jump-to-definition) plus RL beats multi-tool scaffolds [NEW]
arXiv:2512.20957, first submitted 2025-12-24, **latest revision v6 2026-05-26** [23]. A single
execution-aware tool -- jump to the definition of an invoked symbol -- trained end-to-end with RL: the
7B model outperforms 14B baselines, 14B surpasses 32B, and 32B exceeds GPT-5 on most metrics.
- MEASURED (authors'). Borderline "new" -- the v6 revision post-dates earlier sweeps but the paper is
  older; flagged NEW because it never appeared in the 2026-08-12 or 08-14 banks.
- Reads as a **surface-area argument**: the winning scaffold was one precise navigation primitive, not
  a wide toolbelt. That is an argument for the MCP lean-default ladder, from the capability side rather
  than the token-budget side.

### 2.6 Context representation: the source itself, not summaries [NEW]
arXiv:2607.09691, **2026-06-19** [24]. Holding localization fixed with an oracle and varying only the
representation, on SWE-bench Verified: natural-language summaries answer **4/45** behavioural questions
vs **27/45** for the source (held-out repos, independent judge), and a frontier model's summaries score
exactly as poorly as a 3B model's -- "the gap belongs to the representation, not the summarizer".
- MEASURED, protocol frozen before data collection (the authors say so).
- CONFIRMS tg's choice to return **source ranges with line anchors** rather than generated prose
  summaries. Honest caveat: it studies the *act* stage after oracle localization, so it says nothing
  about tg's ranking quality.

Honest null for Q2 overall: **nothing found that beats BM25 + dense RRF cheaply on CPU.** The frontier
moved to *what to retrieve for an agent* (2.2) and *how to serve it warm* (2.3), not to a better fusion.

---

## Q3 -- MCP ecosystem shifts affecting tool-surface design

### 3.1 MCP spec revision 2026-07-28: the protocol went STATELESS. Single biggest item in the sweep. [NEW]
Primary source: the official changelog at `modelcontextprotocol.io/specification/2026-07-28/changelog`,
fetched 2026-08-20 [25]. Major changes relevant to a server like tg:
1. **Sessions removed.** `Mcp-Session-Id` is gone from Streamable HTTP; `tools/list` / `resources/list`
   / `prompts/list` "no longer vary per-connection"; cross-call state uses server-minted handles passed
   as ordinary tool arguments (SEP-2567).
2. **The `initialize` / `notifications/initialized` handshake is removed.** Every request carries its
   protocol version and client capabilities in `_meta`; mismatches return
   `UnsupportedProtocolVersionError` (SEP-2575).
3. **New `server/discover` RPC that servers MUST implement** to advertise supported protocol versions,
   capabilities and identity (SEP-2575).
4. `subscriptions/listen` replaces the HTTP GET endpoint and `resources/subscribe` (SEP-2575).
5. `ping`, `logging/setLevel`, `notifications/roots/list_changed` removed (SEP-2575).
6. Tasks moved out of core into the `io.modelcontextprotocol/tasks` extension; polling `tasks/get`
   replaces blocking `tasks/result` (SEP-2663).
7. **MRTR** (Multi Round-Trip Requests) replaces server-initiated `roots/list` / `sampling` /
   `elicitation`; servers return `InputRequiredResult` (SEP-2322).
8. All results carry a required `resultType` (`"complete"` / `"input_required"`) (SEP-2322).
9. SSE resumability and message redelivery removed; a broken stream loses the in-flight request.

Minor changes that bear directly on tool-surface design:
- Servers **SHOULD** return `tools/list` in a **deterministic order** "to enable client-side caching and
  improve LLM prompt cache hit rates".
- **`ttlMs` and `cacheScope` are now REQUIRED** on `tools/list`, `prompts/list`, `resources/list`,
  `resources/read` and `resources/templates/list` results via a new `CacheableResult` interface
  (SEP-2549).
- `inputSchema`/`outputSchema` loosened to any JSON Schema 2020-12 keywords, with `$ref` resolution
  requirements and composition-keyword resource bounds (SEP-2106).
- A JSON-RPC error-code allocation policy: `-32020`..`-32099` reserved for the spec; several draft codes
  renumbered.
- **Roots, Sampling and Logging are DEPRECATED** (SEP-2577).

MEASURED/AUTHORITATIVE: this is the specification text itself, not a summary of it. Secondary coverage
confirming the industry read (all in the delta window, all analysis, none needed for the facts above):
mcpindex "What is MCP 2.0", 2026-08-18 [26]; byteiota "What Breaks, What to Fix", 2026-08-13 [27];
Nango, 2026-08-13 [28]; Apify's own migration writeup, 2026-08-18 [29]; Scalekit Q&A on auth/identity,
2026-08-14 [30]; Gravitee, 2026-08-13 [31].

**tg's concrete exposure, verified in-tree (not inferred):** `pyproject.toml:586` pins
`"mcp>=1.27.2,<2"`, and the comment at `pyproject.toml:573` records the reason -- **mcp 2.0.0 removed
`mcp.server.fastmcp`**, which `src/tensor_grep/cli/mcp_server.py:20` imports at module scope, guarded by
`tests/unit/test_mcp_dependency_is_upper_bounded.py`. `mcp_server.py:22` imports
`mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS`, i.e. tg delegates protocol-version negotiation to the
SDK entirely. So the stateless spec reaches tg **through the SDK major bump**, and the existing upper
bound is what is currently holding it off. `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"`
(`mcp_server.py:191`) is tg's own contract number and is unrelated to the wire protocol -- do not
conflate them.

### 3.2 Agent Plugins 1.0: an MCP server is now packaged with skills, vendor-neutrally [NEW]
GitHub Changelog **2026-08-12** [32]: Agent Plugins 1.0 was published **2026-08-06** with AWS,
Anysphere, Microsoft, OpenAI and Vercel, Google joining as a core maintainer the same day. "An open
standard that packages agent skills and MCP servers into one installable plugin that is governed
independently of any single vendor." GA in VS Code, Copilot CLI, the GitHub Copilot SDK and the Copilot
app, on all Copilot plans.
- MEASURED as a shipped standard + GA surface (vendor changelog, primary).
- This is a **distribution** shift, not a protocol one: the unit of installation is becoming
  skill+server, which is exactly the shape tg already has (in-repo skill library + MCP server) but
  does not currently publish as one artifact.

### 3.3 Progressive discovery / catalog patterns keep hardening in practice [KNOWN, strengthened]
Delta-window practitioner material: a large-registry post explicitly about "keeping 142 MCP tools
available without loading them all into context" (2026-08-13) [33]; governed MCP tool catalogs for large
engineering teams (2026-08-12) [34]; an MCP tool-discovery deep-dive (2026-08-15) [35]; Google's Gemini
Enterprise "Governing Agent Skills" policy docs (2026-08-13) [36].
- SECONDARY / vendor-docs. Adds no new number to the 2026-08-14 "lean-by-default is spec-level" bank,
  but the *governance* framing (who may install which tools) is new emphasis.
- The spec-side reinforcement is real and is in 3.1: deterministic `tools/list` ordering + required
  `ttlMs`/`cacheScope` are the protocol telling servers to make their tool surface **cacheable**, which
  is the same pressure as lean-by-default arriving through a different door.

---

## Q4 -- what invalidates or strengthens tg's stated moats

### 4.1 Mandato: signed mandates enforced at the MCP protocol level, with chained audit trails [NEW -- closest prior art yet to tg's receipt moat]
arXiv:2608.14074, **2026-08-14** [37]. A governance proxy enforcing "digitally signed mandates on agent
actions at the protocol level": a machine-readable signed artifact specifying which tools an agent may
invoke, under which parameter constraints and contextual conditions, for how long and on whose behalf;
the proxy evaluates every call against the mandate chain, **blocks non-conforming calls in line**, and
records permit/deny plus evidence.
- MEASURED as a system paper (unrefereed preprint; its evaluation numbers were not read this pass --
  honest gap).
- This is **closer to tg's edit-control plane than arXiv:2606.04193 was** (the previous nearest
  neighbour, banked 2026-08-12). Two distinctions still hold and should be stated precisely rather than
  waved at: Mandato is a **proxy in front of tool calls**, tg's control is **inside the edit primitive**
  and survives an agent that never speaks MCP; and Mandato's premise -- "authorization logic lives in
  application code, is neither signed nor independently auditable" -- is an argument *for* tg's posture,
  not against it. Whitespace narrows; it does not close.

### 4.2 Audit trails for coding agents are being productized at the plumbing layer [NEW]
WorkOS, **2026-08-13** [38], shipping `workos-audit-harness` (`npx github:workos/workos-audit-harness`),
which hooks a coding agent's session/prompt/tool lifecycle and emits `session.started`,
`prompt.submitted`, `tool.called`, `turn.completed`, on a shared core across Claude Code, Codex and pi.
Their own framing of the problem: an agent "can read, write and delete files on the machine, and none
of that is recorded anywhere you could query."
- MARKETING (vendor launch), but the artifact is real and installable.
- Important qualifier, and it is the whole moat question: this is **self-logged telemetry from the
  harness**, precisely the forgeability class arXiv:2606.04193 named in the 2026-08-12 bank. It
  commoditizes *observability*, not *attestation*. tg's escrowed CI-held-key receipts are still
  differentiated -- but the market is now being taught that "audit trail" means a free npx hook, which
  is a **positioning** problem even where it is not a technical one.

### 4.3 Prompt injection / approved-tool abuse keeps validating fail-closed contracts [NEW, corroborating]
ARMO, "MCP Prompt Injection: The Attack Uses the Tools You Approved" (2026-08-14) [39]; MCP
tool-handoff abuse, "when tool metadata becomes control" (2026-08-12) [40]; Docker, "Coding Agent Horror
Stories: The Command You Already Approved" (2026-08-18) [41]; Endor Labs on harness engineering
(2026-08-17) [42]; **CVE-2026-75130**, Context7 2.1.2 prompt injection via custom AI instructions
(2026-08-18) [43].
- Mixed: the CVE is a primary registry record; the rest are vendor security marketing.
- All of it points the same way as the 2026-08-12 bank's arXiv:2604.04978 (a deployed classifier gate at
  ~17% false-negative): **approval-at-the-gate is the failing layer**, which is the argument for
  deterministic, contract-level refusal inside the tool. CONFIRMS the fail-closed moat; adds no new
  measurement of it.

### 4.4 Nothing found this sweep invalidates a stated tg moat.
Honest null, stated as a null rather than a clean bill: the closest pressure is 4.1 (protocol-level
signed authorization exists in the literature) and 4.2 (audit-trail language being commoditized). No
2026-08-12..20 source was found showing a competitor shipping **head-bound signed receipts for code
edits**, nor one showing fail-closed backend contracts being abandoned as an approach.

---

## Ranked "so what for tg"

| # | Item | Call | One-line reason |
|---|---|---|---|
| 1 | MCP 2026-07-28 stateless spec (3.1) | **ACT** | The wire protocol tg speaks was restructured (no `initialize`, no sessions, mandatory `server/discover`, required `ttlMs`/`cacheScope`), and tg's only defence today is an `mcp<2` upper bound whose own comment says the import it protects was deleted upstream. |
| 2 | Agent Plugins 1.0 GA across VS Code / Copilot CLI / Copilot SDK (3.2) | **INVESTIGATE** | A vendor-neutral, already-GA package format whose unit is exactly skills+MCP-server, i.e. tg's existing shape, on a distribution surface tg does not ship to. |
| 3 | Mandato, protocol-level signed mandates (4.1) | **INVESTIGATE** | Nearest prior art yet to the edit-control-plane moat; read its evaluation and pin publicly what tg does that a front-of-tool proxy structurally cannot. |
| 4 | Agent Retrieval Bench + SWE-Explore + CORE-Bench (2.2) | **INVESTIGATE** | First external harnesses that score ranked-under-budget, `edit2ripple`, and abstention with wrong-repository controls -- the three things tg does that generic code-search benchmarks punish. |
| 5 | CodeNib incremental-serving numbers (2.3) | **INVESTIGATE** | Supplies the missing quantitative floor for the banked CONTINUOUS-REFRESH scoping row (8.7x/25.4x update speedups; static index 4.7x faster than a live language server), and its direction favours tg's index-not-LSP posture. |
| 6 | "Better Call Grep" index-free lexical retrieval (2.1) | **INVESTIGATE** | Claims ripgrep-driven retrieval matches graph RAG on repo-level completion -- the strongest external challenge yet to the marginal value of `tg find`'s dense half, and worth a measurement before more dense investment. |
| 7 | WorkOS audit harness commoditizing "audit trail" (4.2) | **INVESTIGATE** | Self-logged telemetry is not attestation, but the market is being taught the word for free; a positioning risk to receipts, not a technical one. |
| 8 | Codebase-map/graph launch wave + Atlassian Code Context (1.5, 1.2) | **INVESTIGATE** | Six new map/graph entrants in eight days plus a platform vendor selling the org-graph half means "a map, not a grep" is no longer differentiating on its own. |
| 9 | RepoNavigator one-tool-plus-RL result (2.5) | **LEAVE** | Useful surface-area evidence for the lean MCP ladder, but it is an RL-training result with no consumer asking tg for anything; bank it. |
| 10 | ast-grep at 0.45.1 / 15.6k stars, no metavar-perf demand (1.1) | **LEAVE** | No release since the last sweep and still zero perf-blocked consumer, so the AST-DSL-PARITY reopen trigger is unmet. |
| 11 | Prompt-injection / approved-tool CVE wave (4.3) | **LEAVE** | Corroborates the fail-closed moat and changes nothing about it; no new measurement, no new exposure. |
| 12 | HAKARI-Bench (2.4) | **LEAVE** | Methodologically attractive Pareto harness, but not code-specific, so it cannot arbitrate a `tg find` change. |
| 13 | Sourcegraph Cody enterprise-only (1.4) | **LEAVE** | Single secondary source, no primary confirmation found; not actionable until verified. |

---

## Sources

1. https://api.github.com/repos/ast-grep/ast-grep/releases (GitHub REST, queried 2026-08-20)
2. https://api.github.com/repos/ast-grep/ast-grep (GitHub REST, queried 2026-08-20)
3. https://www.atlassian.com/blog/development/code-context (2026-08-12)
4. https://cursor.com/blog/firetiger (2026-08-13)
5. https://techcrunch.com/2026/08/18/cursor-capitalizes-on-github-frustration-launches-rival-hosting-platform/ (2026-08-18)
6. https://cursor.com/guides/coding-agent-mcp (2026-08-12)
7. https://dev.to/ramdai_bista/cody-review-2026-sourcegraphs-code-intelligence-assistant-is-now-enterprise-only-4h00 (2026-08-17, secondary)
8. https://www.ssdnodes.com/learn/graft-codebase-graph-for-agents (2026-08-13)
9. https://dev.to/hugolesta/give-your-agent-a-map-of-your-codebase-not-a-grep-32km (2026-08-13)
10. https://pypi.org/project/code-graph-rag/ (2026-08-14)
11. https://npm.io/package/repository-intelligence-engine (2026-08-13)
12. https://deepwiki.com/colbymchenry/codegraph/5.1-mcp-tools-reference (2026-08-15)
13. https://github.com/zzet/gortex (2026-08-20)
14. https://github.com/tirth8205/code-review-graph (2026-08-20)
15. https://thenewstack.io/deepseek-harness-open-source-plugins/ (2026-08-13)
16. https://www.infoq.com/news/2026/08/deep-seek-harness/ (2026-08-20)
17. https://arxiv.org/abs/2601.23254 -- "Better Call Grep" (v1 2026-01-30, v3 2026-07-29; arXiv API)
18. https://arxiv.org/abs/2606.11864 -- CORE-Bench (v1 2026-06-10, v2 2026-07-13; arXiv API)
19. https://arxiv.org/abs/2606.07297 -- SWE-Explore (2026-06-05; arXiv API)
20. https://arxiv.org/abs/2607.24882 -- Agent Retrieval Bench (2026-07-27; arXiv API)
21. https://arxiv.org/abs/2607.25431 -- CodeNib (2026-07-28; arXiv API)
22. https://arxiv.org/abs/2606.22778 -- HAKARI-Bench (2026-06-22; arXiv API)
23. https://arxiv.org/abs/2512.20957 -- RepoNavigator / "One Tool Is Enough" (2025-12-24, v6 2026-05-26; arXiv API)
24. https://arxiv.org/abs/2607.09691 -- "What Context Does a Coding Agent Actually Need to Act?" (2026-06-19; arXiv API)
25. https://modelcontextprotocol.io/specification/2026-07-28/changelog (spec revision 2026-07-28; fetched 2026-08-20)
26. https://mcpindex.ai/guides/what-is-mcp-2-0 (2026-08-18, secondary)
27. https://byteiota.com/mcp-2026-07-28-stateless-spec/ (2026-08-13, secondary)
28. https://nango.dev/blog/stateless-mcp-how-it-changes-the-way-agents-call-tools/ (2026-08-13, secondary)
29. https://blog.apify.com/mcp-stateless-migration/ (2026-08-18, secondary)
30. https://www.scalekit.com/blog/mcp-stateless-update (2026-08-14, secondary)
31. https://www.gravitee.io/blog/scaling-ai-agents-key-takeaways-from-the-model-context-protocol-mcp-specification-release (2026-08-13, secondary)
32. https://github.blog/changelog/2026-08-12-agent-plugins-1-0-in-vs-code-copilot-cli-and-the-copilot-app/ (2026-08-12; standard published 2026-08-06)
33. https://dev.to/lovanaut55/keeping-142-mcp-tools-available-without-loading-them-all-into-context-3i2b (2026-08-13)
34. https://www.c-sharpcorner.com/article/building-governed-mcp-tool-catalogs-for-large-engineering-teams/ (2026-08-12)
35. https://dev.to/hypernexus/mcp-protocol-deep-dive-the-intricate-dance-of-tool-discovery-for-ai-agents-2lea (2026-08-15)
36. https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/policies/govern-agent-skills (2026-08-13)
37. https://arxiv.org/abs/2608.14074 -- Mandato (2026-08-14; arXiv API)
38. https://workos.com/blog/audit-trail-for-every-coding-agent (2026-08-13)
39. https://www.armosec.io/blog/mcp-prompt-injection/ (2026-08-14)
40. https://sunglasses.dev/patterns/mcp-tool-handoff-abuse (2026-08-12)
41. https://www.docker.com/blog/coding-agent-horror-stories-the-command-you-already-approved/ (2026-08-18)
42. https://www.endorlabs.com/learn/harness-engineering-how-to-make-ai-coding-agents-reliable-and-secure (2026-08-17)
43. https://notcve.org/cve/CVE-2026-75130 (2026-08-18)
