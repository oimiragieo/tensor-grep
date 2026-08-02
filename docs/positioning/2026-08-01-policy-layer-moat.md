# The policy layer as the moat — assessed against the real code and the real market

**Date:** 2026-08-01 · **Branch:** `research/policy-layer-positioning` · **Status:** research memo, no
product code changed · **Scope:** the three P3 positioning items in `docs/TASK_BOARD.md`.

> **How to read this.** Every internal claim below is grounded in either a symbol in this repo or a
> command I ran against the installed binary, with the output pasted. Every external claim carries a
> URL. Anchors are **symbols with a grep instruction**, never line numbers — per AGENTS.md, "Cite the
> SYMBOL, not the line". Where I could not verify something, it is labelled UNVERIFIED rather than
> written around.

**Provenance of the receipts.** Code reads are against this worktree at `pyproject.toml`
`version = "1.101.30"`. Command receipts are from the binary on `PATH`
(`tg --version` -> `tg 1.101.29`), i.e. one patch release behind the tree. Nothing below depends on
a behaviour that changed between those two, but the split is recorded because a version claim made
from the wrong artifact is the standing trap in this repo.

---

## 0. Headline

| Board claim | Verdict | One-line reason |
|---|---|---|
| Policy layer is the moat | **PARTLY TRUE, and the board's own framing is wrong in two places** | The primitive is real and is genuinely rare. But "2026 market consensus" is **one blog post**, and tg ships **no escalation** — the third of the three things the quote names. |
| Incompleteness-honesty is a differentiator | **TRUE but OVERSTATED as written** | "No competitor documents such a contract" is false: GitHub's Search API ships `incomplete_results`, LSP ships `isIncomplete`. tg's is the strongest version, not the only one. |
| Depth-vs-breadth on languages | **TRUE as a frame, but it does NOT rescue us against Gortex** | Gortex's own docs publish a 3-tier split whose **deep tier is ~30 languages** against tg's 5. Tiering itself is normal industry practice (Semgrep, Sourcegraph, Zed, nvim-treesitter), so honesty here is table stakes, not a moat. |

**The single most valuable finding:** the capability the board calls the moat — `tg prepare` — appears
**zero times in `README.md`**. It is not in the mkdocs published nav either. Positioning work that
does not start by putting it on the front door is rearranging a comparison table around a product
nobody can see.

---

## 1. The honest current state

### 1.1 `tg prepare` — what it actually returns

Grep `def prepare(` and `def _build_prepare_payload(` in `src/tensor_grep/cli/main.py`. Docstring,
verbatim:

> One-call edit-readiness capsule: primary target, confidence, a callers/blast-radius floor,
> validation commands, and claim/evidence coordination hooks -- the single call meant to replace the
> orient -> search -> agent -> route-test -> callers -> evidence -> ledger loop.

Run against this worktree (abridged; the run exited 0):

```
$ tg prepare src/tensor_grep/cli/incompleteness.py "budget_remediable" --json --deadline 25
{
  "routing_backend": "RepoMap",
  "routing_reason": "prepare",
  "primary_target": {
    "file": "...incompleteness.py", "symbol": "budget_remediable", "kind": "function",
    "line": 50, "confidence": 0.9, "evidence": ["parser-backed", "heuristic"]
  },
  "confidence": { "overall": 0.9, "downgrade_reasons": [] },
  "ask_user_before_editing": { "required": false, "reasons": [] },
  "validation_commands": [
    "uv run pytest tests/unit/test_disclosure_covers_every_incompleteness_emitter.py -q",
    "uv run pytest tests/unit/test_incompleteness_markers.py -q",
    "uv run pytest tests/unit/test_budget_remediable_cli_parity.py -q",
    "uv run pytest -q"
  ],
  "blast_radius_floor": { ... "graph_trust_summary": {"edge_kind": "reverse-import",
      "confidence": "strong", "provenance": ["graph-derived", "parser-backed"]} },
  "coordination": { "claim": {...advisory...}, "evidence": {...} },
  "scan_limit": {"max_repo_files": 2000, "scanned_files": 1, "possibly_truncated": false}
}
```

That is a real one-call edit-readiness primitive. It picks a target, scores its own confidence,
names the graph edges backing the blast-radius floor, hands back *detected* validation commands
(each with a `detection` and `confidence` field, so an unverified suggestion is distinguishable
from a detected one), and carries a machine-branchable escalate-to-human flag
(`ask_user_before_editing`). The claim/evidence hooks are advisory-by-default: `submitted: false`
unless `--claim` is passed.

### 1.2 Budget control is real and it is enforced by refusing to lie

`tg prepare` and `tg agent` default to a **60s deadline on the cold path** (grep
`DEFAULT_AGENT_CLI_DEADLINE_SECONDS` in `src/tensor_grep/cli/agent_capsule.py`), plus
`--max-tokens`, `--max-files`, `--max-sources`, `--max-repo-files`. The important part is not the
knobs; it is what happens when a knob binds. Two arms of the same command:

```
# COMPLETE ARM
$ tg prepare src/tensor_grep/cli/incompleteness.py "budget_remediable" --json --deadline 25
EXIT=0
confidence = {"overall": 0.9, "downgrade_reasons": []}
(no "partial" key at all)

# TRUNCATED ARM  (same query, whole src/ tree, 0.1s budget)
$ tg prepare src "budget_remediable" --json --deadline 0.1
EXIT=2
partial          = true
partial_reason   = "deadline"
deadline_limit   = {"deadline_exceeded": true, "capsule": true, "blast_radius_floor": false}
confidence       = {"overall": 0.72, "downgrade_reasons": [
                     "repository scan truncated before ranking completed",
                     "context omitted by token or render budget",
                     "context consistency downgraded confidence"]}
```

Three things move together: the exit code, a boolean, and the **confidence score itself**. A
truncated scan does not merely append a flag — it lowers the number the agent is supposed to gate
on. Grep `_scan_incomplete` in `src/tensor_grep/cli/main.py` for the exit-2 gate, and note the
in-code comment on the ordering: *"print the full payload FIRST, then exit 2 ... never a silent
exit 0 that reads as a complete, full-confidence result."*

The symbol commands hold the same three-state contract. A clean bidirectional-plus-one oracle,
all three arms run:

```
$ tg callers src/tensor_grep/cli budget_remediable --json          EXIT=0   not_found=false result_incomplete=false
$ tg callers src/tensor_grep/cli zzz_no_such_symbol_zzz --json     EXIT=1   not_found=true  result_incomplete=false
$ tg callers src budget_remediable --json --deadline 0.1           EXIT=2   not_found=false result_incomplete=true
```

Exit 1 and exit 2 are genuinely different states, and `not_found` is suppressed on the truncated
arm rather than reported as `false`-meaning-"we found it". That is the whole point of the contract
and it demonstrably works.

### 1.3 What the completeness contract actually is

`docs/CONTRACTS.md` opens with **section 0, "The completeness contract (governs every section
below)"**:

> Every `tg` answer is either complete, or it names exactly how it is incomplete and whether the
> caller can do anything about it.

with three ranked obligations — P1 no silent loss, P2 actionable disclosure (a budget cause is
remediable, an unreadable path is not), P3 surface agreement across CLI / JSON / exit code / MCP —
and two named CI ratchets enforcing it (`tests/unit/test_silent_loss_census_ratchet.py`,
`tests/unit/test_native_walk_error_ratchet.py`).

The vocabulary lives in one module, `src/tensor_grep/cli/incompleteness.py`, whose docstring states
the design point that most competitors get wrong:

> THE DESIGN POINT -- this is an ALLOW-LIST, never a bare `returncode == 2` tolerance. Exit 2 is
> overloaded: it is what an honest incomplete scan returns, but it is ALSO what a catastrophic
> failure returns ... A consumer that tolerates the bare code would swallow every one of those and
> become a check that cannot fail.

`budget_remediable()` in the same module answers "can a bigger budget fix this?" as a fail-closed
allow-list — `unknown` returns `False`, so an unrecognised cause is never answered with "raise the
limit".

**Census:** `result_incomplete` appears at 124 sites across 9 Python modules plus 42 sites in
`rust_core/src/`; `incomplete_reason_class` at 82 sites across 8 Python modules plus three Rust
files (`main.rs`, `native_search.rs`, `gpu_native.rs`). This is not a flag on one command.

### 1.4 The three honest caveats on the incompleteness story

These are the parts a competitor would attack, so they belong in our own doc first.

**(a) There is no single machine-branchable field. There are at least three families.** Measured on
one truncated `tg callers` run:

```
result_incomplete       = true
partial                 = true
deadline_limit          = {"deadline_exceeded": true, "files_scanned": 12, "files_total": 89}
incomplete_reason       = null
incomplete_reason_class = (absent)
partial_reason          = (absent)
scan_remediation        = "The scan stopped at the --deadline before finishing, so a zero or small
                           count is NOT trustworthy. Re-run with a larger --deadline, ..."
```

The run was disclosed, exit-2'd, and handed the caller actionable prose — but the *machine-readable
cause* arrived as `deadline_limit.deadline_exceeded`, not as the `incomplete_reason_class`
vocabulary. `tg prepare` uses a third spelling (`partial_reason: "deadline"`). MCP uses a fourth
(`scan_limit.truncation_cause` + `budget_remediable`). `docs/CONTRACTS.md` documents this and
defends it explicitly (task #293: the vocabularies "are deliberately NOT unified ... renaming
either breaks a documented contract for no correctness gain"). That defence is reasonable
*engineering*. It is a liability in *marketing copy* that says "one field". Say "every surface
discloses, and the contract names which field carries it per surface" — which is true — rather than
"one machine-branchable field", which is not.

**(b) `result_incomplete` alone is not a completeness check, and our own contract says so.**
`docs/CONTRACTS.md` section 4 spells out the trap: a payload can carry `result_incomplete: false`,
`not_found: false`, exit `0`, and `token_budget.primary_omitted: 3` — one of four callers returned,
every field contract-correct. `primary_truncated` is the field that tells you it is a subset. Any
external claim must not imply a single boolean covers this.

**(c) The `--json` payloads for most commands are not independently re-verified at the installed
version.** The workspace CLAUDE.md records the contract re-verification as pinned to 1.101.9, with
`--json` spot-checked on `defs` only. My three-arm exit-code receipt above is fresh at 1.101.29 and
covers `callers` and `prepare`; nothing here claims the other commands' envelopes were re-checked.

### 1.5 Language coverage — derived from the product, not hand-counted

Hand-counting this number has been wrong four times in this repo, so it was derived by calling the
product's own descriptor. Run from the worktree `src/` with the module identity asserted:

```
MODULE: C:\dev\projects\.tg-wt-research\src\tensor_grep\cli\repo_map.py
REGISTRY_COUNT: 10
IDS: ['c', 'cpp', 'csharp', 'go', 'java', 'javascript', 'php', 'python', 'rust', 'typescript']
SCOPE: c-cpp-csharp-go-java-javascript-php-python-rust-typescript
NAV:   parser-backed-refs-callers:go-javascript-python-rust-typescript
     + foundational-defs-imports-only:c-cpp-csharp-java-php
```

(Grep `_symbol_navigation_descriptor` and `_language_scope_descriptor` in
`src/tensor_grep/cli/repo_map.py`. Both derive from `lang_registry.LANGUAGE_REGISTRY` live, so they
cannot drift the way a hardcoded literal did.)

**10 registered / 5 parser-backed refs+callers / 5 defs+imports only. No third tier.** The board's
"10 / 5" is correct.

The tier difference is real, not cosmetic: for the foundational five, `tg refs` / `tg callers` /
`tg blast-radius` fall through to `_regex_references_and_calls`, a text heuristic, rather than an
AST-verified match — stated in `_symbol_navigation_descriptor`'s own docstring.

**This tiering is emitted in every capsule's `coverage.symbol_navigation` field and is published in
no public document.** `README.md` says only that `tg callers` is "Python-first". There is no
language table anywhere a buyer can read.

---

## 2. The three claims, assessed

### Claim 1 — "the policy layer is the moat"

**Verdict: PARTLY TRUE. The primitive is real and rare. Two load-bearing parts of the board's
framing are wrong.**

**WRONG #1 — "The 2026 market consensus is ..." is one blog post.** I fetched the source. The quote
in `docs/TASK_BOARD.md` is verbatim from Ceaksan, *"Code Search for AI Agents: ripgrep, ast-grep, or
Semantic?"*, https://ceaksan.com/en/code-search-for-ai-agents-which-tool-when, published
2026-05-19 — a single-author personal blog. It is a good post and its comparison table marks
`ripgrep`, `ast-grep`, Aider repo-map, Claude Code's context engine and Sourcegraph Amp all with the
same gap: "no policy layer." But the post labels its own numbers *"illustrative numbers, not
measurements"* and its budget heuristic *"not a canonical formula, my own default"*.

Two other 2026 writers agree that lexical+structural+graph retrieval is now table stakes and then
name a **different** next gap: a standard cross-engine query protocol
(https://rywalker.com, 2026-03-15) and ranking quality
(https://dev.to — harrisonsec, 2026-06-08). Three authors, three different "real gaps". That is a
live debate, not a consensus. **Citing it as "the 2026 market consensus" in any public artifact is
a claim we cannot support and would lose on inspection.** Cite the post by name and date, or drop
the appeal to consensus and argue the position on our own evidence.

**WRONG #2 — tg does not ship the escalation the quote names.** The quoted gap is "the orchestration
layer that combines all three with **escalation** and budget control". Measured:

```
grep -rni "escalat" src/  ->  1 hit, and it is a process-kill escalation in session_daemon.py
grep -rni "escalat" rust_core/src/  ->  1 hit
POSITIVE CONTROL: grep -rni "fallback" src/  ->  533 hits   (the grep works)
```

There is no recall check, no zero-result retry at a higher layer, no "results clustered in one file,
expand scope" rule anywhere in the retrieval path. What tg *does* have is `docs/routing_policy.md` +
`rust_core/src/routing.rs` — a real, documented, priority-ordered router — but it selects an
**execution engine** (NativeCpu / NativeGpu / TrigramIndex / AstBackend / Ripgrep / GpuSidecar) for a
query that has already been decided. That is a performance/capability policy, not a
retrieval-strategy escalation policy. Conflating the two in public copy is the kind of claim a
technical buyer checks in ten minutes.

**Also worth stating plainly: `tg prepare` does not fuse the semantic leg.** `agent_capsule.py` and
`repo_map.py` import **zero** of `retrieval_bm25` / `retrieval_dense` / `retrieval_fusion` /
`reranker` (positive control: `agent_capsule.py` does import
`tensor_grep.core.retrieval_lexical.split_terms`, and `main.py` imports all four retrieval modules,
so the grep discriminates). `tg agent --help` offers no `--semantic` flag. Its `route_rationale`
on a live run reads:

```
[{"strategy": "context-render", "evidence": "heuristic"},
 {"strategy": "blast-radius-call-sites", "evidence": "python-ast"}]
```

So the capsule fuses a **lexical term-overlap heuristic** (`retrieval_lexical.score_term_overlap`),
**structural** tree-sitter defs, **graph** import/caller edges, and **file centrality**
(`orient_capsule._file_centrality_scores`). Under Ceaksan's own taxonomy — which classes repo-map
centrality as the semantic layer and embeddings as a separate last resort — that *is* the three
layers. But BM25 + dense RRF lives in a **separate command** (`tg find`), unreachable from
`prepare`. If we claim "combines all three in one call", a reviewer who runs `tg agent --help` will
find no semantic knob and conclude we overstated. Say what is true: **`tg prepare` fuses lexical,
structural and graph evidence into one budget-bounded, self-scoring answer; the dense/BM25 hybrid is
`tg find` and is not yet wired into it.**

**What survives, and it is strong.** External research found **no code-search vendor** positioning
itself as a policy/orchestration/escalation/budget layer — every one of Gortex, Serena,
claude-context, grepai, CodeGraph, GitNexus, Sourcegraph, Augment, agentmako positions as a better
index or better context engine. Policy-layer products that do exist (Axor, Facio, govAgent) govern
*general agent tool execution*, not search-backend arbitration. And on the one-call edit-readiness
primitive specifically, the closest analogues each cover **two of four** elements:

| Tool | Target | Confidence | Blast radius | Validation commands | Source |
|---|---|---|---|---|---|
| `tg prepare` | yes | yes | yes | yes (detected, per-command confidence) | this repo |
| CodeGraph `codegraph_explore` | yes | no | yes | no | https://codegraph.codes |
| GitNexus `impact` | no | yes | yes | no | https://github.com/abhigyanpatwari/GitNexus |
| Aider repo map | ranked list | no | no | no | https://aider.chat/docs/repomap.html |

Nobody found bundles all four. **That is the real, defensible claim** — and it does not need the
Ceaksan quote to stand up.

### Claim 2 — "incompleteness-honesty is a differentiator"

**Verdict: TRUE as a differentiator, OVERSTATED as written. The sentence "no competitor surveyed
documents a contract of this form" is false in a way a reviewer will find in one search.**

Two real counterexamples:

- **GitHub REST Search API** ships a **required** `incomplete_results: boolean` on every search
  response: *"For queries that exceed the time limit, the API returns the matches that were already
  found prior to the timeout, and the response has the `incomplete_results` property set to `true`."*
  It even documents the inverse honestly: *"Reaching a timeout does not necessarily mean that search
  results are incomplete."* https://docs.github.com/en/rest/search/search (feature traces to a
  2014 changelog). I fetched and confirmed this text directly.
- **LSP** ships `CompletionList.isIncomplete` — *"This list is not complete. Further typing should
  result in recomputing this list"* — plus a dedicated `CompletionTriggerKind.TriggerForIncompleteCompletions`
  and a general `PartialResultParams` mechanism.
  https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/

What is genuinely ours, and holds up:

1. **The exit code agrees with the payload.** Neither GitHub's boolean nor LSP's has a process exit
   code, because neither is a CLI. Against the actual CLI comparators: ripgrep's exit 2 conflates a
   regex syntax error with a soft per-file read error and carries no JSON field distinguishing them
   (https://github.com/BurntSushi/ripgrep/blob/14.0.3/doc/rg.1.txt.tpl).
2. **A reason class with a remediability verdict.** `incomplete_reason_class` is a closed vocabulary
   (`unreadable_path` / `scan_limit` / `deadline` / `timeout`) and `budget_remediable()` tells the
   caller whether a retry is worth anything. Nobody surveyed publishes an equivalent. This is the
   part with no counterexample at all.
3. **It is contract-level and CI-enforced**, not per-endpoint. Section 0 of `CONTRACTS.md` governs
   every other section, with two named ratchets.
4. **MCP — the protocol our own MCP surface rides on — has no standardized equivalent.** As of the
   current stable spec, `CallToolResult` carries only `content` / `structuredContent` / `isError`.
   Partial results are in-flight proposals: PR #776 (opened 2025-06-18, still open) and SEP-1905
   (2025-11-26). https://github.com/modelcontextprotocol/modelcontextprotocol/pull/776 ·
   https://modelcontextprotocol.io/specification/2025-06-18/schema . *We are ahead of the protocol
   here*, which is a better story than "nobody does this" and is checkable.

agentmako's freshness labels (`live` / `fresh_indexed` / `stale` / `historical` / `contradicted` /
`unknown`, https://agentmako.drhalto.com/docs/concepts/freshness.html) are the nearest named
analogue and answer a **different question**: is a cached answer still valid given file changes?
That is temporal validity, not whether *this call* finished. They compose; they do not compete. The
board calling them "weaker" is imprecise — call them orthogonal.

**Rewrite the claim as:** *"Incompleteness disclosure exists in fragments across the industry —
GitHub's search API, LSP completions. tg is the only code-search tool we found that makes it a
contract governing every surface, pairs it with a remediability verdict, and makes the process exit
code agree. MCP has not standardized it yet."* That is defensible, more specific, and more
impressive than the absolute.

### Claim 3 — "depth-vs-breadth, stated honestly"

**Verdict: the frame is TRUE, the numbers are RIGHT, and the frame does NOT win the argument. Two
corrections.**

**Correction 1 — tiered language-support disclosure is normal industry practice.** Stating our tiers
honestly is table stakes, not a differentiator:

- **Semgrep** publishes a formal four-tier maturity system (GA / Beta / Experimental / Community)
  with measurable thresholds — parse rate 99%+ / 95%+ / 90%+ / 90%+ and differing syntax support.
  https://semgrep.dev/docs/references/language-maturity-levels (2026-04-30) — fetched and confirmed.
- **Sourcegraph** runs a documented three-tier navigation fallback: Precise (SCIP/LSIF) → Syntactic →
  Search-based, with the UI marking which tier produced a result.
  https://sourcegraph.com/docs/code-navigation/precise-code-navigation
- **Zed** splits its public page into 59 LSP-supported vs 16 syntax-highlighting-only languages.
  https://zed.dev/languages
- **nvim-treesitter** tiers every grammar `stable` / `unstable` / `unmaintained` / `unsupported`.
  https://github.com/nvim-treesitter/nvim-treesitter/blob/main/SUPPORTED_LANGUAGES.md

What *is* above the median: our direct MCP-code-search peers (Serena, claude-context, CodeGraph,
code-graph-mcp, ast-grep) publish a **flat count with no tiering**. So we are better than the peer
group and merely normal against the wider tooling industry. Position it that way.

**Correction 2 — and this is the loud one — Gortex already publishes a tiered breakdown, and its
deep tier is six times ours.** From Gortex's own `docs/languages.md`, which I fetched:

> Gortex currently indexes **256 languages**. ... Three engine tiers are used, in order of decreasing
> extraction depth:
> - **bespoke tree-sitter** (~30 languages) — full concrete syntax tree ... high-fidelity symbols,
>   **resolved call edges**, ORM/contract/dataflow extraction ...
> - **regex** (~60 languages) ...
> - **forest signature-only** (~165 languages) — ... **No** ORM / contract / dataflow / scope-aware
>   resolution ...

https://github.com/zzet/gortex/blob/main/docs/languages.md

The apples-to-apples comparator for tg's 5 parser-backed-refs-callers languages is Gortex's ~30
bespoke tree-sitter languages **with resolved call edges** — which includes all five of ours plus
Java, C#, PHP, C, C++ (our foundational tier) plus Kotlin, Swift, Scala, Ruby, Elixir, Dart, OCaml,
Lua and more. **Reframing to depth does not close a 5-vs-30 gap; it relocates the same gap into the
tier we would rather be measured on.** Any public doc that says "ours are deeper" without this
number is setting up a rebuttal that writes itself.

Two things that legitimately help, both checkable:

- Gortex's README and site say **257** languages while its own `docs/languages.md` says **256**, with
  a table totalling 256. A one-off internal inconsistency; worth knowing, not worth leading with.
- Their published "Core programming — deep extraction" table is a `Full`/`Partial` self-rating, not a
  measured recall figure. Ours is derived live from `LANGUAGE_REGISTRY`, so it cannot drift. That is
  a *methodology* advantage, and methodology is the honest ground to fight on.

**UNVERIFIED and stated as such:** I found no independent benchmark comparing tg's resolved-edge
quality against Gortex's on a shared corpus, and no user complaint of the form "claims N languages,
only works on M" naming any tool in this niche (queries and the positive control proving the search
apparatus works are in §5). **The depth claim is currently an architectural argument, not a measured
one.** Until it is measured, write it as an architectural argument.

---

## 3. What a rewritten `docs/tool_comparison.md` should say

Do **not** edit that file on this branch. This is the proposed shape.

**The diagnosis.** The current file is a benchmark table with a comparator-policy appendix. It opens
by comparing tg to `ripgrep`, `ast-grep`, `Semgrep` and `Zoekt` on **latency**, and its own honest
rows say `rg` wins cold search on this host and every validated parity row is slower than pinned
`rg`. That honesty is right and must stay — but it means the document's own headline evidence argues
we are a slower grep. Counted rather than estimated: **7 workload rows** in the Public Comparison
Snapshot plus **8 timing rows** in the Host-Local Command Snapshot — 15 rows, every one of them
speed or parity, none of them edit-readiness or answer-trustworthiness. `grep -ci "prepare"` -> **0**;
`grep -ci "incomplete"` -> **0** (positive control: `grep -ci "ripgrep"` -> 6).

**Proposed structure.**

1. **Reframe the opening in one paragraph.** Today it reads *"tensor-grep should not be described as
   a single universal winner over every other search tool."* Keep that discipline and add the axis:
   *"and speed is not the axis it is built for. tg's comparators split into two groups: engines it is
   measured against on latency (`rg`, `ast-grep`, `Zoekt`, `Semgrep`), and agent-facing code-context
   tools it is positioned against on edit-readiness and answer trustworthiness (Serena, Gortex,
   CodeGraph, GitNexus, claude-context, Sourcegraph, Augment). This document has only ever covered
   the first group."*

2. **New lead section: "One call to edit-readiness."** The `tg prepare` payload from §1.1, abridged,
   with the four-element table from §2/Claim 1 showing which competitors cover which elements. Each
   competitor row carries its URL. Lead with the capability, not the benchmark.

3. **New section: "What the answer tells you when it could not finish."** The three-arm 0/1/2
   receipt from §1.2 verbatim, the `budget_remediable` allow-list, the `scan_remediation` string —
   and, in the same section, honest caveats (a) and (b) from §1.4. A trust document that hides its
   own trust caveats is self-defeating. Comparator column: ripgrep's overloaded exit 2, GitHub's
   `incomplete_results`, LSP's `isIncomplete`, MCP's absent field, agentmako's orthogonal freshness
   axis.

4. **New section: "Language coverage" — a real table, published for the first time.** Two tiers
   derived from `_symbol_navigation_descriptor`, plus a stated comparator row for Gortex's ~30
   bespoke / ~60 regex / ~165 signature-only and Serena's 40+ via wrapped LSP servers. State the
   5-vs-30 gap in our own words before someone else does. Add: "this table is generated from
   `LANGUAGE_REGISTRY`, not hand-maintained" — and then actually generate it, or a governance test
   will be pinning prose again.

5. **Demote the existing benchmark tables into "Engine-level performance"**, unchanged. They are
   good and they are honest. They are just not the lead.

6. **Extend the Comparator Policy section** — currently scoped to speed comparators — to cover
   capability comparators: *no head-to-head capability claim against a named agent-facing tool
   without a dated URL to that tool's own documentation for the claim, re-checked at the time of
   writing.* Every competitor claim in this memo carries one; make that the rule.

**And before any of it: put `tg prepare` in `README.md`.** It has three sibling commands in the
quick-start block and is absent. `grep -c "tg agent" README.md` -> 3; `grep -in "prepare" README.md`
-> 0 matches. That asymmetry is the finding.

---

## 4. What we canNOT claim

**Mandatory section. Every line here is a claim that would fail review.**

1. **The 7.5x-fewer-tokens-than-grep benchmark (#72) is CEO-GATED for public use.** It is listed
   under `docs/TASK_BOARD.md`'s CEO-GATED heading and in `docs/BACKLOG.md`. It is referenced in this
   memo for internal reasoning only. **Publishing it is not my call and not any agent's call.** It
   must not enter `docs/tool_comparison.md`, `README.md`, a blog post, or any external artifact
   without an explicit CEO go. Note also that it belongs to the same metric family competitors
   already publish (grepai 97% input-token cut, CodeGraph 94% vendor / ~70% independently re-measured
   fewer tool calls, GitNexus 88%, Gortex up to 50x) — so it will be read *comparatively*, which
   raises rather than lowers the bar on how it is framed.

2. **We cannot claim "the 2026 market consensus".** One blog post, 2026-05-19, plus two contemporaries
   naming different gaps. Cite the post or argue from our own evidence.

3. **We cannot claim tg does retrieval escalation.** Measured absent, with a positive control. We may
   claim engine routing (`routing_policy.md`) and budget control, both real.

4. **We cannot claim `tg prepare` fuses semantic/dense retrieval.** BM25+dense RRF is `tg find` and is
   not reachable from `prepare`. Claim lexical + structural + graph + centrality, which is true.

5. **We cannot claim "no competitor documents an incompleteness contract".** GitHub Search API and
   LSP are counterexamples. Claim the narrower, stronger version in §2/Claim 2.

6. **We cannot claim "one machine-branchable field".** There are at least three disclosure vocabularies
   and `CONTRACTS.md` deliberately keeps them separate (#293).

7. **We cannot claim tiered language disclosure is novel.** Semgrep, Sourcegraph, Zed and
   nvim-treesitter all do it. We may claim it is above the median for our direct peer group.

8. **We cannot claim our 5 deep languages beat Gortex's ~30 on depth.** No shared-corpus measurement
   exists. The architectural argument (resolved edges vs signature-only) applies to their *forest*
   tier, not their *bespoke* tier, where they overlap us and exceed us in count.

9. **We cannot beat `rg` on cold search and must not imply it.** `docs/TASK_BOARD.md` records that as
   a closed honest negative: tg's native walk *is* rg's walk, same `ignore` crate.

10. **The `--json` envelopes of most commands are not freshly contract-verified at 1.101.29/.30.**
    This memo's receipts cover `prepare`, `agent` and `callers` only.

---

## 5. Method notes and negative controls

Recorded so the negatives in this memo are legible rather than assumed. A zero with no control is
not a result.

| Probe | Result | Control proving it discriminates |
|---|---|---|
| `grep -rni "escalat" src/ rust_core/src/` | 1 + 1 hits, both unrelated | `grep -rni "fallback" src/` -> 533 |
| `retrieval_bm25|dense|fusion|reranker` in `agent_capsule.py`, `repo_map.py` | 0 | same modules import `tensor_grep.core.retrieval_lexical`; `main.py` imports all four |
| `prepare` in `README.md` | 0 (case-insensitive) | `tg agent` in `README.md` -> 3 |
| Language tier count | derived by calling `_symbol_navigation_descriptor()` | `MODULE:` printed and asserted to be the worktree file, not an installed wheel |
| exit-code contract | 0 / 1 / 2 on three arms | all three arms run; each returned a *different* code with agreeing JSON |
| prepare completeness | exit 0, no `partial` key | truncated arm exit 2, `partial: true`, confidence 0.9 -> 0.72 |

**One probe failed first and is recorded because the failure mode recurs on this box.** Redirecting
`tg` output to `/tmp/...` from Git Bash and reading it with Windows Python produced
`FileNotFoundError` — a path-domain split, not a tool failure. All receipts above were re-run with a
Windows-domain scratch path. A run that "produced no file" is not a run that produced nothing.

External searches that returned nothing useful, with the queries, per the same rule:

- No code-search vendor positioning as a policy/orchestration/escalation layer. Queries:
  `"policy layer" agent code search escalation budget "not another index" positioning 2026`;
  `"policy layer" OR "orchestration layer" code search agent budget escalation lexical structural graph 2026`.
  Control: those queries *did* return relevant governance products (Axor, Facio, govAgent) — the
  searches work, the code-search-scoped claim simply does not exist.
- No independent user complaint of the form "claims N languages, only works on M" naming any tool in
  this niche. Query: `"claims" languages "only works" reddit hacker news code tool language support overstated`.
  Control: the same query shape returned high-engagement threads on the adjacent
  benchmark-claims-vs-reality topic, so the apparatus is live.
- No shared-corpus benchmark comparing tg to Gortex/Serena/CodeGraph on retrieval quality.

---

## 6. Open questions a human must answer

1. **Is `tg prepare` the product's headline, or a power feature?** Everything in §3 assumes the
   former. If it is the latter, the comparison document should stay speed-led and this memo's §3 is
   wrong. This is a product decision, not a research finding.
2. **Do we publish the 5-vs-30 language gap ourselves?** Naming a competitor's stronger number in our
   own doc is the honest move and is consistent with how this repo treats negatives — but it is a
   marketing judgement with a real cost, and it is not mine to make.
3. **Does #72 get published, and in what frame?** CEO-gated. If yes, it lands in a market where four
   competitors already publish the same metric family and one of them (CodeGraph) has been
   independently re-measured *below* its vendor claim. That context changes how the number should be
   framed.
4. **Do we build the escalation, or drop the word?** Two coherent answers: (a) implement a real
   query-classify -> escalate -> recall-check loop and then claim the full policy position, or
   (b) claim budget control + engine routing + one-call readiness, which we genuinely have. Option
   (a) is a build; option (b) is a doc edit. This memo does not choose.
5. **Should the disclosure vocabularies be unified?** `CONTRACTS.md` #293 says no, on
   backwards-compatibility grounds, and that reasoning is sound. But it costs us the cleanest version
   of our best claim. A third option exists — a single derived `incomplete: {complete, cause,
   remediable}` object emitted *alongside* the existing fields, additive and non-breaking. That is a
   design decision with a compatibility cost, and it needs an owner.
6. **Who owns generating the language table?** If §3 item 4 ships as hand-written prose it will rot,
   and this repo has a documented history of exactly that. Generated from `LANGUAGE_REGISTRY`, or not
   at all.
