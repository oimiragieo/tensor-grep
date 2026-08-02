# Investigation: MCP tool consolidation (backlog #98)

- **Date:** 2026-08-01
- **Base:** `origin/main` @ `f49b81c` (branch `research/mcp-tool-consolidation`)
- **Scope:** ANALYSIS + DESIGN only. No consolidation implemented.
- **Verdict up front:** **KILL #98 as written.** The headline work already shipped
  (v1.81.0, 2026-07-17), the board's tool count is stale, the board's env-var name does not
  exist in the codebase, and "non-breaking" is true of the shipped half and definitionally
  false of the unshipped half. Re-file the residue as three separate, honestly-scoped items —
  one of which is a real defect that should ship regardless of any consolidation decision.

---

## 0. The board entry, and what is wrong with it

`docs/BACKLOG.md` (the `#98` bullet under "CURRENT LIVE BACKLOG") reads:

> **#98** MCP tool consolidation (45->~10 task-shaped dispatch tools, non-breaking,
> `TG_MCP_TOOL_SURFACE=lean`) + staleness receipts (P2). Design previously recovered/verified
> (campaign #142). Note: `#554`/v1.67.1 shipped a much narrower precursor under the same tracking
> number (`tg_session_open` default `max_repo_files` 512->2000) — that is NOT this consolidation.

Four things in that sentence are wrong, and they compound: they make an item that is ~80% done
read as an item that has not started.

| Board claim | Reality | How derived |
|---|---|---|
| "45" tools | **58** advertised today; **48** immediately before the consolidation landed | `mcp.list_tools()`, §1 |
| "`TG_MCP_TOOL_SURFACE=lean`" | **That env var does not exist.** The shipped knob is `TG_MCP_LEGACY_TOOLS`, and it takes off-tokens (`0`/`false`/`no`/`off`), not the value `lean` | §2 |
| Implicitly unbuilt / "demand-gated" | **Phase-1 SHIPPED** — PR #643, commit `6d8a23e`, released v1.81.0 on 2026-07-17, contract `1.3.0`→`1.4.0`, documented, tested | §2 |
| "non-breaking" | True of what shipped (purely additive). **False** of the only thing left to do (flipping the default removes 46 tool names from the wire) | §4 |

### 0.1 The "45" was true once, and the board never noticed it going stale

`45` was not invented. Counting `^@mcp.tool()` decorators at the commit that first wrote the
figure into the board:

```
7be8e80 (2026-07-09): 45 tools   <- the commit that wrote "45" into BACKLOG.md
099a0c7 (2026-07-11): 47 tools
888a556 (2026-07-13): 47 tools
6d8a23e^ (2026-07-17): 48 tools  <- immediately pre-consolidation
6d8a23e  (2026-07-17): 58 tools  <- Phase-1 lands
```

So the figure was accurate for **two days** and has been wrong for **three weeks**, across the
very release that changed it. The board also *knows* about #643 — its 2026-07-2x reconcile
narrative names "#643 MCP consolidation Phase-1" in a prose paragraph — but the live `#98`
bullet was never updated to match. This is the workspace's own recurring failure mode: a number
in prose with no derivation beside it. A board line that carries a count should carry the command
that regenerates it.

### 0.2 The one sub-claim that is not merely stale but undefined

"+ staleness receipts (P2)" appears **exactly once in the entire repository — in that backlog
line itself.** Grep across `docs/`, `src/`, `tests/` finds no design, no plan, no code, no test,
no prior audit. (Positive control: the same grep for `staleness` returns 10+ files, so the search
works; the term exists, this *use* of it does not.) There is no recoverable specification for
this sub-item. It cannot be scoped, so it cannot be estimated, so it should not sit inside a
tracking number as if it were queued work.

---

## 1. The derived tool count

**Source of truth:** the FastMCP registry, i.e. exactly what a client receives from `tools/list`.
Not a decorator grep, not the board, not a docstring.

Method: import `tensor_grep.cli.mcp_server` from this worktree's `src/` (with `__file__` asserted,
per the stale-venv law) and enumerate `await mcp.list_tools()`.

```
MODULE_FILE       C:\dev\projects\.tg-wt-mcp\src\tensor_grep\cli\mcp_server.py
CONTRACT_VERSION  1.7.0

ARM A  TG_MCP_LEGACY_TOOLS unset (the default)   ->  58 tools
ARM B  TG_MCP_LEGACY_TOOLS=off                   ->  12 tools
```

Composition of the 58, from `mcp_server.py`'s own module-level tuples
(`_PYTHON_LOCAL_MCP_TOOLS`, `_EMBEDDED_SAFE_MCP_TOOLS`, `_NATIVE_REQUIRED_MCP_TOOLS`,
`_SINGLETON_MCP_TOOLS`, `_META_MCP_TOOLS`):

| group | n | gated by `TG_MCP_LEGACY_TOOLS`? |
|---|---|---|
| legacy python-local | 42 | yes |
| legacy embedded-safe | 2 | yes |
| legacy native-required | 2 | yes |
| **legacy subtotal** | **46** | **yes** |
| always-on singletons (`tg_mcp_capabilities`, `tg_classify_logs`) | 2 | no |
| task-shaped meta-tools | 10 | no |
| **total advertised (default)** | **58** | |
| **total advertised (`=off`)** | **12** | |

### 1.1 Controls — why these numbers are not the "measured nothing" kind of number

Every count above is paired with a control, because a tool-enumeration probe that silently
imports the wrong module or returns an empty list yields a believable, wrong number.

1. **Bidirectional set diff, not just a count.** `declared − advertised` = `[]` **and**
   `advertised − declared` = `[]` in Arm A. A count alone would pass even if the registry and the
   module's own tuples disagreed about *which* 58.
2. **A known-present name.** `tg_mcp_capabilities` is asserted present in both arms; the probe
   exits non-zero and prints `PROBE_BROKEN` if it is absent. A zero-tool return can therefore
   never be reported as a result.
3. **The arms differ.** Arm B returns 12, not 58. A probe that returned the same number under
   both flag states would be inert — it would prove only that *something* was enumerated, not
   that the enumeration tracks the thing under study. This is the discriminating control for the
   whole document: it proves the flag is real and load-bearing, not decorative.
4. **The historical counts** in §0.1 use `git grep -c` at each revision, with a deliberately
   impossible pattern run as a control (it returns no match / exit 1), so a `0` from a broken
   pattern is distinguishable from a `0` meaning absence. The pre-Phase-1 era predates
   `_register_legacy_tool`, so `^@mcp.tool()` was the complete registration surface at those
   revisions and the method is valid for them — it is **not** valid at HEAD, where it undercounts
   by 46.

---

## 2. What already shipped (the finding that reframes the item)

Phase-1 of #98 is **in production and has been since v1.81.0 (2026-07-17)**, commit `6d8a23e`,
PR #643.

What it delivered:

- **10 task-shaped meta-tools**, always registered: `tg_navigate`, `tg_impact`, `tg_query`,
  `tg_context`, `tg_explore`, `tg_session`, `tg_scan`, `tg_audit`, `tg_checkpoint`, `tg_rewrite`.
  Each takes an `action: str` selector and dispatches to the legacy function directly.
- **`TG_MCP_LEGACY_TOOLS`** (`mcp_server.py::_legacy_tools_enabled`), default **ON**, evaluated
  once at import time so registration and the capabilities payload can never disagree within a
  running process.
- **`mcp_server.py::_register_legacy_tool`**, which returns `fn` *completely unchanged* when the
  flag is off — so a de-advertised legacy name stays callable in-process and the meta tools keep
  working regardless of flag state.
- A contract bump `1.3.0 → 1.4.0` with a written rationale.
- Documentation in `docs/harness_api.md` (which already states "58 tools by default" and
  documents the flag and every meta-tool signature) and `CHANGELOG.md`.
- Tests including **subprocess-isolated** flag-OFF de-registration
  (`test_mcp_legacy_tools_flag_off_deregisters_legacy_tools_subprocess`), off-token recognition,
  a clean-partition invariant (`test_meta_and_singleton_tool_names_partition_cleanly`), and
  per-meta dispatch spies.

So the question "should we consolidate?" is not open. It was answered, built, reviewed, released
and documented. **The only open question is whether to flip the default.** That is a much smaller,
much better-specified decision, and the board does not describe it.

### 2.1 Coverage: is the lean surface actually complete?

If any legacy tool were reachable only by its own name, flipping the default would delete
capability. Derived, not assumed:

- The `composes` map across all 10 meta-tools covers **46 of 46** legacy tools. Zero orphans,
  zero double-composition (`COMPOSED_BY_MULTIPLE_METAS` = `{}`). It is a clean partition.
- But a `composes` list is a *declaration*, and a declaration is satisfied by a comment. So the
  claim was re-derived **behaviourally**, by AST-walking the module for actual calls to legacy
  functions from inside meta-tool bodies: **46/46 legacy tools are genuinely invoked** — 42
  directly inside the meta `FunctionDef`s, and the remaining 4 (`tg_search`, `tg_ast_search`,
  `tg_find`, `tg_index_search`) inside the shared helper `mcp_server.py::_tg_query_dispatch`,
  which `tg_query` delegates to.
- **Parameter forwarding:** every parameter every legacy tool accepts is forwarded by its
  composing meta-tool. Nothing is dropped.
- **Behavioural spot-check with a negative control:** `tg_navigate(action="imports", file=…)`
  returns a payload **byte-equal** to `tg_file_imports(file=…)` on the same input, and
  `tg_navigate(action="definitely_not_an_action")` returns `error.code = "invalid_input"` rather
  than a result. The negative arm matters: without it, "the meta tool returned something" is not
  evidence the dispatcher discriminates.

**Two corrections to my own probes, recorded because a clean result from a blind instrument is
the failure mode this repo keeps paying for:**

1. The parameter-forwarding walk first reported **5 dropped `max_tokens` params** across
   `tg_context` and `tg_session`. Reading the call sites showed all five pass it via
   `**max_tokens_kwargs` — a splat, whose `ast.keyword` carries `arg=None`, which my walk skipped.
   The true count is **0 dropped**. Had I not read the matches, this document would have reported
   a capability gap that does not exist.
2. The call-site walk first reported **42 of 46** legacy tools invoked, i.e. 4 apparently
   declared-but-dead. Those 4 live one level down in `_tg_query_dispatch`, outside the meta
   `FunctionDef` my walk traversed. The true count is **46**.

Both were false negatives produced by a probe that ran successfully and printed a plausible
number. Neither was caught by re-reading the source; both were caught by reading the specific
matches the probe flagged.

---

## 3. Does a large tool list actually cost anything?

### 3.1 tg's own measured wire size

Serializing exactly what `tools/list` returns (`name` + `description` + `inputSchema`, compact
JSON):

| arm | tools | wire bytes | est. tokens (chars/4) |
|---|---|---|---|
| default (`TG_MCP_LEGACY_TOOLS` unset) | 58 | **81,365** | ~20,300 |
| lean (`TG_MCP_LEGACY_TOOLS=off`) | 12 | **30,498** | ~7,600 |
| delta | −46 | **−50,867 (−62.5%)** | ~−12,700 |

Bytes are the primary number. The token column is a chars/4 **estimate** and is labelled as such:
no tokenizer for the target model was available offline, and published measurements show the same
schema costing ~173 tok/tool on Opus-class models versus ~64–72 on GPT/Gemini-class — a ~2.7×
spread, so the true token figure is model-dependent and could differ materially in either
direction.

The cost is concentrated, not uniform. The three largest entries in the default surface
(`tg_query` 4,989B, `tg_ruleset_scan` 4,599B, `tg_session` 4,364B) each cost more than 25× the
smallest (`tg_rulesets` 180B).

### 3.2 External evidence (every claim URL-cited)

**Anthropic states the threshold explicitly, and tg is over it:**

> "A typical multiserver setup … can consume ~55k tokens in definitions before Claude does any
> work… **Claude's ability to pick the right tool degrades once you exceed 30–50 available
> tools.**"
> — <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>

The same doc gives a rule of thumb that deferred loading pays off above "10+ tools" or ">10K
tokens of definitions". tg's default surface is **58 tools / ~20K est. tokens** — over both. The
lean surface is **12 tools / ~7.6K est. tokens** — under the token threshold, marginally over the
tool one. Anthropic's engineering write-up gives a worked five-server example at 58 tools /
~55K tokens (<https://www.anthropic.com/engineering/advanced-tool-use>, 2025-11-24) — coincidentally
the same tool count as tg's default, though tg's descriptions are terser.

**Independent studies confirm degradation is real, not vendor marketing:**

- HumanMCP (arXiv 2602.23367): controlled 10/50/100-tool experiment across three models,
  ~9–10 point accuracy drop from 10→100 tools.
- RAG-MCP (arXiv 2505.03275): retrieval-augmented tool selection vs full-context prompting —
  43.13% vs 13.62% accuracy, roughly halved prompt tokens.
- "How Many Tools Should an LLM Agent See?" (arXiv 2605.24660): 87.1% selection accuracy with a
  fixed 5-tool list vs 93.1% with an adaptive ~2.2-tool list; over-presentation measurably hurts
  even when the correct tool is present.
- ToolScope (ACL 2026 anthology): merging/filtering toolsets improved selection accuracy by
  8.4–38.6% across Seal-Tools, UltraTool and BFCL.

**Client caps — tg exceeds exactly one of them:**

| client | cap | behaviour past cap | tg @58 |
|---|---|---|---|
| VS Code / Copilot Chat | **128** (hard, thrown in shipped code) | explicit error, request rejected | fine |
| Windsurf Cascade | 100 (across all servers) | silent drop | fine alone |
| Cursor IDE | **~40** | **silently drops tools**, no error | **over** |
| Cursor CLI | stricter, model-dependent | explicit "Too many MCP tools are enabled" error | likely over |
| Claude Code / Desktop | none documented | n/a — ships `ENABLE_TOOL_SEARCH` instead | fine |
| Cline | none stated; maintainer notes reliability drop past ~20 | — | over the soft figure |

Sources: <https://forum.cursor.com/t/about-limitation-of-the-number-of-mcp-tools/107844>,
<https://forum.cursor.com/t/cli-mcp-tool-limits/165642>, VS Code issues #290356 / #253539 /
#248021, <https://docs.windsurf.com/plugins/cascade/mcp>, cline/cline discussion #3081. The VS
Code number is the best-attested (a literal `length > 128` throw in shipped source, cited across
four issues); the Cursor and Windsurf numbers are community/first-party-forum rather than a spec
page.

**But mitigations already exist client-side, and this is the load-bearing counterweight:**

- The MCP spec has supported cursor-based `tools/list` pagination and `listChanged` notifications
  since 2025-03-26 — <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>
- MCP's own client-best-practices doc (2026-07-28) recommends *client-side* progressive discovery
  and explicitly says "some model providers already offer built-in tool search… When available,
  you may prefer the platform's tool search over a custom implementation" —
  <https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices>
- Anthropic's Tool Search Tool + `defer_loading` (GA) works **per MCP server** via
  `mcp_toolset.default_config.defer_loading`, keeps tools registered but out of the cached prefix,
  and is designed not to break prompt caching.
- OpenAI Agents SDK ships `ToolSearchTool()`, `defer_loading=True`, and `tool_namespace()` —
  <https://openai.github.io/openai-agents-python/tools/>
- VS Code auto-groups tool sets behind `activate_*` stubs when over threshold.
- Every major client supports per-tool/per-server enable/disable.

Caveat found in the record and worth carrying: adoption is uneven — Cursor, Windsurf and Gemini
CLI reportedly still load all definitions upfront, so the protocol-level mitigation does not reach
every client in 2026.

**And consolidation-by-`action`-parameter is itself contested — with Anthropic on the pro side:**

> "Consolidate related operations into fewer tools. Rather than creating a separate tool for every
> action (`create_pr`, `review_pr`, `merge_pr`), group them into a single tool with an `action`
> parameter. Fewer, more capable tools reduce selection ambiguity."
> — <https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools>

Against it, several third-party guides call the same shape a "God Tool" anti-pattern, arguing a
freeform `action` string is a guardrail liability because the schema cannot reject a hallucinated
value at the contract layer. tg's implementation partially answers that objection — an unknown
action returns a structured `invalid_input` envelope naming the valid options
(`_meta_unknown_action_error`, verified behaviourally in §2.1), which is the closed-vocabulary
discipline this workspace requires of any registry-grounded parameter. It does not answer it
fully: the JSON schema advertises `action: str`, so the model is not *schema*-constrained to a
valid value, only *response*-corrected after guessing wrong.

**Net read of the evidence.** Large tool lists do cost something, and tg's default surface is over
Anthropic's stated degradation threshold and over Cursor's cap. But the industry's answer in 2026
has moved to *client-side deferred loading*, not server-side collapse — and Anthropic's own
mitigation is per-server, meaning a client can already get the benefit from tg without tg changing
anything. That materially weakens, though does not eliminate, the case for a server-side default
flip.

---

## 4. Blast radius — what "non-breaking" would really require

The board says "non-breaking". That is true of Phase-1 and false of Phase-2, and conflating them
is what makes the item look safe.

**Phase-1 (shipped) was genuinely non-breaking:** purely additive. 10 new names appeared, nothing
moved, every legacy signature was untouched, and the flag defaulted ON.

**Phase-2 (flipping the default to OFF) is a breaking wire change, by definition.** 46 tool names
disappear from `tools/list`. Any client that hardcoded `tg_search` or `tg_symbol_defs` — which is
every client that used the server before 2026-07-17 — stops finding them. There is no version of
that which is non-breaking. The honest framing is "breaking, with a migration path", and the
migration path is good: `tg_mcp_capabilities()` keeps every meta-tool's `composes` array, so a
client can mechanically derive `tg_search → tg_query(action="text")` even after the legacy names
are gone.

### 4.1 The real defect this investigation found

**The MCP contract version cannot distinguish the two surfaces.** Measured in both arms:

```
ARM A  58 tools  ->  mcp_contract_version = "1.7.0"
ARM B  12 tools  ->  mcp_contract_version = "1.7.0"
```

`tg_mcp_capabilities()`'s payload keys are identical in both arms
(`cli_version`, `embedded_rewrite`, `mcp_contract_version`, `mcp_protocol_version`,
`mcp_supported_protocol_versions`, `native_tg`, `routing_backend`, `routing_reason`,
`schema_version`, `sidecar_used`, `tools`, `version`) — there is **no** `tool_surface` field, no
`legacy_tools_enabled` field, and no per-tool deprecation marker anywhere in `tools[]`.

This directly contradicts the module's own written rationale for why bumps exist. From the
`1.2.0 → 1.3.0` note in `mcp_server.py`'s contract-version comment block:

> "bumped because `tg_mcp_capabilities()`'s `tools[]` array grew again, which a version-pinning
> client may want to detect (else **two different tool sets would both report 1.2.0 and a pinning
> client would not re-fetch tools[]**)"

The flag creates exactly that condition. Two different tool sets — 58 and 12 — both report
`1.7.0`. A version-pinning client that caches on `mcp_contract_version` and connects to an
operator who flipped the flag will believe it has 58 tools and silently have 12.

This is not hypothetical severity: it is the same class as the `1.6.0 → 1.7.0` lesson recorded
three paragraphs above it in the same file — *a pass-through handler makes the producer it wraps
a wire surface*. Here, an **environment variable** is a wire surface, and the contract version does
not track it.

**This should be fixed regardless of what happens to #98.** It is small (an additive
`tool_surface: "full" | "lean"` field on the capabilities payload, plus a contract bump to
`1.8.0`), it is defensive, and it makes any future default flip *discoverable* rather than silent.
Per this repo's own rule, a flag whose state no consumer can observe is a flag that will surprise
someone.

### 4.2 The other thing a default flip would need

`tools[]` carries no deprecation signal. In the default-ON state today, a client has no way to
learn that `tg_search` is the legacy surface and `tg_query(action="text")` is the forward one.
A deprecation window needs that signal to exist *before* the flip, not at it — otherwise the
"window" is a changelog entry nobody read.

---

## 5. Recommendation

**KILL #98 as written.** Its headline is already shipped, its count is stale, its named env var
does not exist, its safety claim applies to the wrong half, and one of its two sub-items has no
recoverable specification. It cannot be prioritised honestly in that state — a reader deciding
what to work on next is looking at a description of a different piece of work.

Re-file the residue as three items with independent dispositions:

### 5a. `tool_surface` disclosure on the MCP capabilities payload — **PROCEED (small, do it)**

Additive `tool_surface: "full" | "lean"` (or equivalent) on `tg_mcp_capabilities()`, contract bump
`1.7.0 → 1.8.0`. Closes a real gap where two tool sets report the same contract version, which the
file's own comment block says bumps exist to prevent. Not gated on any consolidation decision;
it makes the decision *safe to take later*. Standard 4-site + contract-bump discipline applies.

### 5b. Flip `TG_MCP_LEGACY_TOOLS` default to OFF (Phase-2) — **PARK, with a named un-park trigger**

Genuinely demand-gated, and the demand question is now precise instead of vague. Un-park on **any
one** of:

1. **A real consumer reports tool-selection trouble** against the default surface, or an operator
   asks for the lean surface. (Currently: no evidence of a single external MCP consumer exists in
   this repo. See §6.)
2. **A target client's cap bites.** Concretely: someone runs tg's MCP server in **Cursor**, whose
   ~40-tool ceiling tg's 58 already exceeds *and which drops the excess silently*. That is the
   single strongest live argument for the flip, because a silent drop is indistinguishable from
   a missing feature.
3. **The surface grows past ~70 tools**, at which point the cost is no longer marginal against
   Anthropic's stated 30–50 degradation band even for clients with deferred loading.

Do **not** un-park on aesthetics or on "58 is a lot". Prerequisites before any flip: 5a must ship
first (so the flip is discoverable), plus a deprecation marker in `tools[]` (§4.2), plus a
dogfood run against the published wheel with the flag off. And it should be framed as **breaking
with a migration path**, never as "non-breaking".

### 5c. "Staleness receipts" — **KILL outright**

No design, no plan, no code, no test, no prior audit anywhere in the repo. It is a phrase, not an
item. If the capability is genuinely wanted, it should be re-proposed from scratch with a stated
problem; carrying an undefined sub-clause inside a tracking number makes the number
un-estimatable and hides that fact.

### What would make me say PROCEED on the whole thing

A single receipt that tg's MCP server is being run by anyone, in a client with a cap below 58, and
that a tool it needed was dropped. That is one bug report away. Nothing in this investigation found
it, and absent it, the flip is a breaking change bought with a theoretical benefit that the client
side can already deliver without us.

---

## 6. What I could NOT determine

Stated plainly, because each of these is a place where a confident answer would have been made up.

1. **Whether anyone actually consumes tg's MCP server.** This is the *entire* demand question and
   it is unanswerable from inside the repository. There is no telemetry, no usage log, no issue
   citing an MCP tool name. "Demand-gated" has therefore never been tested — it has only ever been
   *asserted*. My recommendation to PARK 5b rests on the absence of evidence, which is not the
   same as evidence of absence, and I am not treating it as such.

2. **The real token cost, as opposed to the byte cost.** No tokenizer for any target model was
   available offline. The 81,365 / 30,498 byte figures are exact; the ~20.3K / ~7.6K token figures
   are `chars/4` estimates. Published measurements show a ~2.7× spread in tokens-per-schema across
   model families, so the true figure could sit meaningfully either side of my estimate. The
   *ratio* (−62.5%) is more robust than either absolute.

3. **Whether tg's own 58-tool surface degrades tool-selection accuracy.** I ran no selection eval.
   The 30–50 threshold is Anthropic's general claim about their models, not a measurement on tg's
   specific tool names and descriptions — whose semantic separation might be better or worse than
   average. Anyone citing "tg is over the threshold" as proof of harm is citing a general prior,
   not a measurement of this product.

4. **Whether all 46 meta-tool actions behave identically to their legacy counterparts.** I proved
   the composition map is a clean 46/46 partition, that all 46 legacy functions are genuinely
   invoked (AST-verified across meta bodies plus `_tg_query_dispatch`), that no parameter is
   dropped, and that **one** pairing (`tg_navigate(action="imports")` vs `tg_file_imports`)
   returns a byte-equal payload with a working negative control. I did **not** execute the other
   45. The repo's per-meta dispatch spy tests cover this in CI; I did not run the suite (no venv
   in this worktree, and building one risked the shared-server constraint).

5. **Cursor's and Windsurf's current caps from a first-party spec page.** The VS Code 128 figure is
   solid (a literal throw in shipped source, cited across four issues). Cursor's ~40 and Windsurf's
   100 come from vendor forums and third-party write-ups, corroborated across independent 2026
   sources but not from a versioned vendor doc. If un-park trigger 5b.2 is ever invoked, re-verify
   Cursor's number against the client in hand before acting on it — a cap is exactly the kind of
   figure that goes stale the way "45" did.

6. **Whether `TG_MCP_TOOL_SURFACE` was ever a real name.** It appears nowhere in the repo's history
   that I searched, only in the backlog line. It may have been a design-doc name that the
   implementation renamed, or it may never have existed. Either way the board should carry the
   name that is in the code.

---

## Appendix: reproducing the counts

```bash
# 1. advertised tool count, both arms (assert __file__ before believing anything)
python - <<'PY'
import asyncio, json
from tensor_grep.cli import mcp_server as m
print(m.__file__, m._TG_MCP_SERVER_CONTRACT_VERSION, m._legacy_tools_enabled())
names = sorted(t.name for t in asyncio.run(m.mcp.list_tools()))
assert names, "PROBE BROKEN: zero tools"
assert "tg_mcp_capabilities" in names, "PROBE BROKEN: control absent"
print(len(names))
PY
# then re-run with TG_MCP_LEGACY_TOOLS=off -- it MUST return a different number,
# or the probe is not observing the thing under study.

# 2. historical counts (valid only pre-6d8a23e, before _register_legacy_tool existed)
git grep -c "^@mcp.tool()" <rev> -- src/tensor_grep/cli/mcp_server.py
```
