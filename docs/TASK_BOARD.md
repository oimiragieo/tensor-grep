# tensor-grep — Task Board

> **The operational one-pager.** `docs/BACKLOG.md` is the historical ledger (long, append-only,
> release-by-release); THIS file is the live queue a session or subagent works down, one item at a
> time. Keep it in sync with the CLI task store (`TaskList` / `TaskUpdate`) and with
> `gh pr list` — GitHub is the source of truth for PR state, this file is the source of truth for
> WHAT IS NEXT AND WHY.
>
> **Rules for anyone (human or agent) working this board:**
> 1. Take the top unblocked item in the highest-priority section. Do not cherry-pick easy ones.
> 2. Every item needs a **bidirectional oracle** before it is done — state what the test shows on
>    the PRE-FIX baseline. A test that passes in both arms is not evidence (AGENTS.md, oracle forms).
> 3. Move an item to DONE only with a PR number AND a merged commit. "Verified locally" is not done.
> 4. If an item turns out to be a non-defect, move it to **RETIRED** with the reason — a documented
>    retirement is worth as much as a fix, because it stops the next session re-chasing it.
> 5. `fix:`/`feat:` PRs RELEASE. Merge one per publish and wait for it. `docs:`/`test:`/`chore:`
>    do not release and may batch. **The merge gate is "no runs in flight on main", full stop** —
>    `tag == PyPI` cannot distinguish *released* from *not started* from *died* and cost a release
>    on 2026-07-28.

Last reconciled: **2026-08-01** (backlog campaign: 7 stale-open items closed), post-**v1.102.0** (PyPI verified via the JSON API, not inferred
from a tag — `tag == PyPI` cannot tell *released* from *not started* from *died*).

**This has now gone stale THREE times in the same way, so the pattern is the finding.** The stamp
once read "2026-07-28, post-v1.101.9" while PyPI had moved 13 releases on; then "2026-07-31,
post-v1.101.22" while PyPI served **v1.101.27** and the IN FLIGHT table below still listed **all
three** of #872/#871/#868 as open — every one of them merged, two of them on 2026-08-01. Each time,
the board was corrected *because someone noticed*, and the correction added a sterner warning rather
than anything that could fire on its own.

**A warning that has been ignored three times is not a weak warning, it is the wrong instrument.**
The reconcile step belongs in the merge routine — the same turn the PR merges, before the next item
is picked up — not in a cleanup pass that only happens when the board embarrasses someone. Derive
both numbers, never retype them:

```bash
gh pr list --state open --json number,title            # the IN FLIGHT table, verbatim
python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/tensor-grep/json'))['info']['version'])"
```

**Why this is NOT a CI gate, deliberately** — recorded so the next session does not build it and
then wonder why it got disabled. Two candidate mechanisms were considered on 2026-08-01 and both
were rejected:

- *Assert the IN FLIGHT table matches `gh pr list`.* Needs network and a GitHub token inside the
  test run. A rate-limited or offline run fails for a reason unrelated to the repo, and a gate that
  reds the build for environmental reasons teaches everyone to reach for `--no-verify` — which
  discredits every other gate here, including the ones catching real defects.
- *Assert the "post-vX.Y.Z" stamp matches `pyproject.toml`'s version.* Zero network and perfectly
  deterministic — and it would fire after **every single release**, several times a day, forcing a
  board edit into every unrelated PR. That is an over-eager rule, and an over-eager rule is worse
  than no rule.

The rule stays DECLARED on purpose. What changed is the *routine* (reconcile inside the merge step)
and the *affordance* (the two commands above, so nobody retypes a number from memory). Harden a
rule when a violation is mechanically detectable without interpretation AND a false positive would
be rare; neither holds here.

---

## IN FLIGHT (PRs open right now — derived from `gh pr list`, 2026-08-01)

| PR | Title | Type | State |
|---|---|---|---|
| #882 | `test`: gate skill-library citation + stated-count drift, and fix the oracle-form miscount | non-releasing (`test:`) | CI green; carries the drift gate, `/tg-skill-audit`, and 6 skill/doc repairs |

*(#872, #871 and #868 all MERGED — #871 on 2026-07-31, #872 and #868 on 2026-08-01. They sat in
this table as "CI running" / "BLOCKED — do not merge" after landing, which is the exact failure mode
described above: a board that says BLOCKED about shipped code will eventually stop someone from
merging something correct.)*

---

## P1 — external dogfood findings (a real user hit these)

- [x] **Anonymous `--claim`** — RESOLVED, and the resolution is the load-bearing part: the sentinel
  STAYS. An adversarial audit killed the auto-derive option with a receipt — `_find_overlaps`
  suppresses when `new.agent_id != _DEFAULT_AGENT_ID and entry.agent_id == new.agent_id`, so two
  zero-config agents sharing a *derived* id would silently drop each other's overlaps, reproducing
  #845 by a new mechanism in the ledger's primary use case. Nor can any derivation escape it: a
  per-checkout id conflates agents, a per-process id is not stable across one agent's calls. Agent
  identity is not derivable from the environment. The SIGNAL got louder instead (`NOT attributable`
  + a machine-branchable `agent_id_is_anonymous`). Pinned by
  `tests/unit/test_anonymous_claim_signal.py`. *Task #13/#23.*
- [x] **Ledger Slice 2 rollup parity** — RESOLVED (`record`/`find` now canonicalise through
  `_ledger_physical_root`). *Task #14.*
- [x] **MaxSim late-rerank is advertised but unexercised** — RESOLVED. Both named defects are fixed: `find_command`'s docstring now states plainly that MaxSim is NOT reachable by a documented path, and `tests/unit/test_find_command.py` asserts the real ORDER inversion rather than only `exit_code == 0`. Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — — *Task #15.* **Now decidable, and the
  answer is DOC-HONESTY, not CUJ coverage.** `tg install-dense` installs `tensor-grep[semantic]`;
  MaxSim needs a DIFFERENT extra (`rerank`) whose model is fetched by
  `python -m tensor_grep.core.retrieval_late --fetch` — **no `tg` command reaches it**. A CUJ would
  first have to build an install path that does not exist, for a stage deliberately held as
  measurably regressing. Two real defects to fix instead: (a) `find_command`'s docstring advertises
  MaxSim while the only control is the undocumented `TG_LATE_RERANK=1`; (b)
  `tests/unit/test_find_command.py` claims to prove the stage "DOES probe/run … observably
  reordering" but asserts only `exit_code == 0` / non-empty / no-exception — **all three hold with
  the env unset**. Its stub already inverts the ranking, so the fix is a one-line ORDER assertion
  with a genuine red arm. `docs(find):`, non-releasing.
- [x] **Bare `tg search P --json` with no PATH** — RESOLVED across all routes. Four dispatch routes
  and then FIVE JSON emitters, fixed one at a time over four releases because each fix closed the
  route that happened to be reported. Both populations are now held by enumerating tests
  (`test_every_search_dispatch_route_discloses.py`,
  `test_scope_note_covers_every_json_emitter.py`). The fifth emitter is the lesson:
  `normalize_gpu_sidecar_json` builds the document by hand with `serde_json::json!()`, so a census
  keyed on `#[derive(Serialize)]` reported "4 of 4 covered" and was wrong by one. **Enumerate
  EMITTERS, not the mechanism they happen to use.** PR #871.
- [ ] **GPU exit-2 calibration — BLOCKED, twice over.** *Task #22.* (1) The CAUSE of #868's CI
  failure is still unknown. A two-arm control proved that IF `resolve_native_tg_binary()` returns a
  path, `main.py:7877` `sys.exit`s ~530 lines before #868's rule at `main.py:8408`, reproducing CI
  byte-for-byte — but `.github/workflows/ci.yml:688` says `test-python` never builds that binary, so
  the mechanism is *sufficient*, not *confirmed operative*. A CI-only diagnostic with a positive
  control is in flight. (2) The PREMISE is contract-contested: exit 2 means INCOMPLETE, and that
  search ran to completion and returned its match; the capability is already in-band via
  `native_gpu_unavailable` / `gpu_evidence_status`. Needs a contract decision, not more code.

## P2 — audit queue (deep audit `wf_38d4b580-d89`, 2026-07-28)

Six read-only lenses — security, CI/release workflow, disclosure edge cases, dead/unwired code,
test trustworthiness, scale-correctness — each finding adversarially verified before it lands here.

That placeholder sat unfilled for three days. Replaced with the items from the 2026-07-31 plan
review, each verified against the real code (a finding without a `file:line` was discarded):

- [x] **CWE-88 argv sentinel — a live hole in a sweep recorded as CLOSED.** `#20`-B2.
  `agent_capsule.py::_agent_gpu_evidence` appended a caller-supplied `evidence_path` as a bare
  positional; clap's `path` (`rust_core/src/main.rs:694-695`) has no `allow_hyphen_values`, so a
  dash-leading path becomes an OPTION and the probe queries a scope nobody chose — **still
  reporting `ok`**. #860 fixed the sibling and the class was marked done; this site was in nobody's
  grep. Now held by `tests/unit/test_argv_sentinel_covers_every_builder.py` (5 builders, by symbol).
  PR #872.
- [x] **`--quiet` silently dropped by both internal rg-passthrough branches** — RESOLVED by `cfc3264`, which moved `-q` out of the shared `_build_cmd` into streaming-only `search_passthrough` (`backends/ripgrep_backend.py`); `test_quiet_survives_rg_passthrough.py` covers both arms. **This entry sat OPEN for months after the fix and is the worked example in `docs/audits/2026-08-01-task-board-staleness.md`** of why a citation gate cannot catch content drift: its `main.py:7937-7943` citation still resolves perfectly. ORIGINAL TEXT FOLLOWS — (`main.py:7937-7943`,
  `:8004-8017`; zero "quiet" mentions in `ripgrep_backend.py`). A flag the caller passed that the
  chosen engine ignores, with no disclosure — the silent-downgrade class.
- [x] **RETIRED, not fixed: the two `--gpu-device-ids` gates are NOT in contradiction.** The entry
  claimed `_can_passthrough_rg` excluding the flag and `_can_delegate_to_native_tg_search`
  including it could not both be right. They can, because they gate DIFFERENT CALLEES:
  - `_can_passthrough_rg` (`main.py:5467`) hands the query to **rg**, which has no GPU. Excluding
    the flag is what stops a silent CPU downgrade at exit 0 with no `fallback_reason` -- its own
    comment says exactly that.
  - `_can_delegate_to_native_tg_search` (`main.py:3813`) hands it to the **native tg binary**,
    which DOES accept `--gpu-device-ids` (declared at `rust_core/src/main.rs:395` and `:650`, and
    forwarded by `_build_native_tg_search_command:3828-3833`).

  So each gate routes GPU work AWAY from the engine that cannot do it and TOWARD the one that can.
  That is the same policy expressed twice, not two policies fighting.

  **The finding was a category error: same flag, different callees.** It survived two passes
  because both sites mention `gpu_device_ids`, and a grep that matches the same identifier in two
  places looks like a contradiction until you read what each one is gating. Recorded rather than
  silently deleted -- a documented retirement is worth as much as a fix (board rule 4), and this
  one would otherwise be re-derived every time someone greps that flag.
- [x] **`--ndjson` zero-match discloses nothing in-band in EITHER engine** — RETIRED, not a defect. The Python emitter deliberately stays silent on a COMPLETE zero-match so readers are not trained to expect a record every run; the Rust divergence is documented at `core/json_fmt.py` (see the comment above the summary emitter). Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — The summary record now
  carries the scope note (#871), but a zero-match `--ndjson` still emits no reason field.
- [x] **The three `main.rs` envelope literals have no direct test** — PARTIALLY REFUTED. Two of three carry serialization tests; the third is `#[cfg(feature="cuda")]` and its exclusion is justified in-code (`cuda-feature-check` runs `cargo check`, not `cargo test`). Verified 2026-08-01 — the finding as written overstated the gap. ORIGINAL TEXT FOLLOWS —, and the CUDA one is
  type-checked but never executed anywhere in CI (`cuda-feature-check` runs `cargo check`, not
  `cargo test`). Checking is not running.
- [x] **THE POPULATION IS THE RECURRING DEFECT, NOT THE ASSERTION.** #872's enumerating test had
  its population wrong **three times in one day** — 5 members, then 8, then 10 — and every miss was
  the same judgement: *"builder A transitively covers builder B."* Each was disproved the same way,
  by deleting B's guard and watching the suite stay green. The third miss,
  `ast_wrapper_backend.py::_build_command`, is the only one in the sweep whose regression is
  **destructive**: a caller-supplied path of `-U`/`--update-all` reaching ast-grep's `run`
  subcommand is its auto-fix switch, so a read-only scan becomes a file rewrite. Two derived rules,
  now in the test's own docstring: **do not add a member by reasoning it is covered — call it**; and
  **a guard whose placement is config-conditional has as many members as it has configurations**
  (the run form's sentinel lives in the `else:` of `if stdin_enabled`, so the default arm alone was
  sampling). A third, from the same review: **a COUNT is blind to an ORDER SWAP** — two members
  asserted `len(tail) == 2` and passed when the pattern and path were exchanged, which in production
  searches a directory named like the pattern for a pattern that is the temp path.
- [x] **`AGENTS.md:1437` is stale on the argv sweep** — REFUTED; already corrected. AGENTS.md now records the sweep-is-now-a-test conversion and the `_agent_gpu_evidence` hole #872 found. Verified 2026-08-01. ORIGINAL TEXT FOLLOWS — — it describes the class as swept while #872
  was open against it. A doc that certifies a sweep it cannot verify is the prose-rung failure this
  board keeps re-learning.

## P3 — strategic / positioning (informed by Exa competitive research, 2026-07-28)

- [x] **Articulate the policy layer as the moat -- CLOSED 2026-08-02.** The item's actual ask was *"tool_comparison.md still positions tg mostly as a search comparator; reframe around one-call edit readiness"* -- delivered by PR #899: the doc now leads with an axis split and carries "One Call To Edit Readiness" + "What The Answer Says When It Could Not Finish", and README mentions edit readiness 4x (was 0). All three falsified claims were kept OUT: 0 occurrences of "market consensus", 0 of "escalat" (PR #900 dropped even the honest `ask_user_before_editing` usage, because our own research said the word invites a rebuttal), and no claim that `prepare` fuses the semantic leg. **RESIDUAL IS A CEO DECISION, NOT WORK:** whether to BUILD an escalation layer or leave the word dropped. Tracked on the CEO-gated list, not here -- an item whose only remaining step is a decision is not an eligible work item, and leaving it open sends agents at finished work. ORIGINAL TEXT FOLLOWS --  — FRAMING CORRECTED 2026-08-01, read `docs/positioning/2026-08-01-policy-layer-moat.md` BEFORE writing any copy.** Verdict: PARTLY TRUE. `tg prepare` genuinely is rare (no surveyed tool bundles target + confidence + blast radius + validation commands in one call), and budget control is real (measured: `--deadline 0.1` -> exit 2, `partial: true`, confidence downgraded 0.9 -> 0.72 with named reasons). BUT three parts of the quote below are NOT ours to say: (a) "the 2026 market consensus" is ONE single-author blog post whose own numbers are self-labelled *illustrative, not measurements*; (b) **tg ships NO escalation** -- 2 occurrences in tracked code, both unrelated (control: `fallback` = 725); `docs/routing_policy.md` is an ENGINE router, not a retrieval-strategy escalation policy; (c) `tg prepare` does NOT fuse the semantic leg -- `agent_capsule.py` imports zero of bm25/dense/fusion (control: it does import `retrieval_lexical`). **Fix the product or drop the word; do not ship the quote.** ORIGINAL TEXT FOLLOWS — The 2026 market consensus is that lexical +
  structural + graph are all table stakes and *"there is no shortage of tools, there is a shortage
  of POLICY — the orchestration layer that combines all three with escalation and budget control is
  the real gap."* `tg prepare` and `tg agent` ARE that layer, and `docs/tool_comparison.md` still
  positions tg mostly as a search comparator. Reframe around one-call edit readiness.
- [x] **Name incompleteness-honesty as a differentiator -- CLOSED 2026-08-02, PR #899** at the NARROW defensible width, not the falsified absolute. `tool_comparison.md` now carries "What The Answer Says When It Could Not Finish": exit-code/payload agreement with a rerunnable two-arm receipt, `budget_remediable()`'s fail-closed allow-list, contract-level scope + CI ratchets -- and GitHub's `incomplete_results` and LSP's `isIncomplete` NAMED as prior art rather than denied. Verified: 0 occurrences of "market consensus" in the doc. ORIGINAL TEXT FOLLOWS --  — CLAIM NARROWED 2026-08-01.** The contract is real and deep (`result_incomplete` at 124 Python + 42 Rust sites; `incomplete_reason_class` at 82 + 3; `budget_remediable()` is a fail-closed allow-list). BUT "no competitor documents such a contract" is **FALSE** and would lose on inspection: GitHub's REST Search API ships a REQUIRED `incomplete_results` boolean with near-identical doc language, and LSP ships `CompletionList.isIncomplete`. The defensible narrower claim (no counterexample found): exit code AGREEING with payload + a closed reason-class vocabulary WITH a remediability verdict + contract-level scope with two CI ratchets -- and MCP has not standardized this at all. Also note we do NOT have "one machine-branchable field": at least three vocabularies exist and CONTRACTS.md #293 defends the split deliberately. ORIGINAL TEXT FOLLOWS — No competitor surveyed (Gortex, Serena,
  claude-context, grepai, CodeGraph, Sourcegraph, Augment) documents a contract of the form
  *"a surface that cannot finish must say so, in a machine-branchable field, with the exit code
  agreeing."* agentmako's freshness labels (live/fresh_indexed/stale/contradicted/unknown) are the
  nearest analogue and are weaker. This is a real moat and it is currently invisible outside the repo.
- [ ] **Token-economics is the category's scoring metric.** Competitors publish token-reduction
  numbers (grepai 97% input-token cut, CodeGraph ~70% fewer tool calls, GitNexus 88%, Gortex 3–50×).
  tg's own measured **7.5× fewer tokens than grep** is the same metric family. Publication is
  **CEO-gated (#72)** — not an AI-doable item, listed so it is not forgotten.
- [x] **Language coverage gap, stated honestly -- SHIPPED #902 (2026-08-02).** Closed by the
  premise check, not by new work: `docs/tool_comparison.md` on `main` already carries the
  two-tier table (both tiers computed live from the language registry, with a "re-derive this
  table; do not trust it" note), the competitor breadth numbers, AND the harder 2026-08-01
  revision this entry was updated to demand -- a **"Where other tools are ahead, stated
  plainly"** section conceding roughly **5 vs 30 on the deep tier, in Gortex's favor**, and the
  explicit framing that tiered disclosure is *table stakes, not a differentiator*. Nothing was
  left to dispatch. 18th stale entry this session; found by running Step 0 of
  `verify-plan-against-code` (is the work still needed?) before dispatching an agent at it.
  The 257-vs-256 discrepancy this entry flagged is RESOLVED (Exa, 2026-08-02): **Gortex's own
  docs disagree with each other** -- `docs/languages.md` says "currently indexes 256" and its
  table totals 256, while the README, `docs/features.md` and gortex.dev say 257. Both of our
  numbers had a real source. `docs/tool_comparison.md` now cites the RANGE and names the
  contradiction rather than picking a side. Same self-contradiction class we spent this session
  fixing in our own docs -- worth knowing it is not unique to us. Also re-verified while there:
  their bespoke tier does contain all ten of `tg`'s languages, so our "deep tier covers all ten
  plus roughly twenty more" line is correct as written.

- [x] **`tg prepare` is invisible** -- **CLOSED 2026-08-02, PR #899.** Measured before and after: README `tg prepare` 0 -> 6; `tool_comparison.md` `prepare` 0 -> 5, `incomplete` 0 -> 8 (control: `ripgrep` 6, unchanged). Added the real one-call invocation with a verified payload, plus two new sections. All 15 speed/parity rows left intact -- they are true. ORIGINAL TEXT FOLLOWS --  The capability the board calls the moat appears **zero times in `README.md`** (control: `tg agent` appears 3x) and sits in no mkdocs-nav'd page except `CONTRACTS.md`. `docs/tool_comparison.md`'s 15 data rows are ALL speed or parity: `grep -ci prepare` -> 0, `grep -ci incomplete` -> 0 (control: `ripgrep` -> 6). Surface the product before repositioning around it -- rewriting a comparison table for something nobody can see is the wrong order. Found 2026-08-01, `docs/positioning/2026-08-01-policy-layer-moat.md`.

## P4 — carried backlog (from `docs/BACKLOG.md`, still open)

- [x] **#58** promote `tg route-test` hidden -> public -- **ALREADY DONE, verified 2026-08-01.** `tg --help` lists it (`route-test  Diagnose routing agreement between context-render...`), it is pinned in `PUBLIC_TOP_LEVEL_COMMANDS` (`tests/e2e/test_routing_parity.py:75`), and no `hidden` marker exists on it anywhere in `cli/main.py`. Found by verifying the item BEFORE dispatching work against it -- the 10th stale-open entry this campaign. ORIGINAL TEXT FOLLOWS --  → public
- [x] **#98** MCP tool consolidation -- **KILLED AS WRITTEN 2026-08-02, re-filed as three.** Phase-1 ALREADY SHIPPED 2026-07-17 (`6d8a23e`, v1.81.0: 10 meta-tools + `TG_MCP_LEGACY_TOOLS` + contract bump 1.3.0->1.4.0). The item's own numbers were fiction: "45" was true for two days and was never right after filing (real: 48 pre-consolidation, 58 now), and **`TG_MCP_TOOL_SURFACE=lean` DOES NOT EXIST** -- its only occurrence in the repo is that backlog line. "non-breaking" is true of what shipped and definitionally FALSE of what remains (the default flip removes 46 names from the wire). Re-filed: (a) a contract-disclosure fix -- 58 tools and 12 tools both report `mcp_contract_version 1.7.0`, so a pinning client cannot tell the surfaces apart; (b) the Phase-2 default flip, PARKED (2026 clients solve this client-side via deferred loading); (c) "staleness receipts", KILLED -- the phrase appears nowhere else in the repo. Evidence: PR #894, `docs/investigations/2026-08-01-mcp-tool-consolidation.md`. ORIGINAL TEXT FOLLOWS --  (45 → ~10 task-shaped dispatch tools, non-breaking)
- [x] **#141** native `AstBackend` vs ast-grep wrapper -- **INVESTIGATED AND FIXED 2026-08-01/02.** The divergence is REAL and its failure mode is a SILENT WRONG ANSWER, not a refusal: a bare word that is also a tree-sitter node-type name is read as a structural query by native and as a code-pattern by the wrapper; both return results, neither warns (PR #890, with a lock-in test that asserts native matches lines not containing the token). Two proven defects then FIXED in #892: `tg scan` called a DRIFTED duplicate `_select_ast_backend_for_pattern` missing its sibling's fail-closed guard (collapsed to a forwarding shim -- one implementation, nothing left to drift), and a `cast(ComputeBackend, ...)` bare-name that would `NameError` at runtime inside an `except` handler. Also found: a previously undocumented THIRD AST engine (`rust_core/src/backend_ast.rs`) which already gives the correct answer on the default path, so full DSL parity stays correctly demand-gated. ORIGINAL TEXT FOLLOWS --  — DSL divergence
- [x] **#160** v1.71.3 dogfood feature tail -- **RECONCILED 2026-08-02.** Four of five named sub-features were ALREADY SHIPPED, verified against the live CLI with positive AND negative controls (`suggested_ignore` returns a value on a vendor tree and `None` on a clean repo). The one genuine gap -- `getattr(mod, "Symbol")` bucketed as plain `string-literal` with no dedicated classification -- is closed in PR #893 as a new `getattr-arg` occurrence value. Correctly judged NOT an MCP contract bump: `string_refs[].occurrence` was never a closed vocabulary. ORIGINAL TEXT FOLLOWS --  (`suggested_ignore`, orient auto-deweight)
- [x] **#115** symlink sweep — CLOSED. `docs/BACKLOG.md` already carried the KILLED/CLOSED verdict; THIS BOARD was the stale copy (verified 2026-08-01).
- [x] **#125** checkpoint `except Exception` → `except BaseException` — CLOSED. Same as #115: `docs/BACKLOG.md` was right, this board was the stale copy (verified 2026-08-01).
- [x] **#143 / #155** Opus-gate LOW follow-ups -- **BOTH ALREADY CLOSED, verified 2026-08-02** (PR #891). Six sub-items, each traced to the commit that closed it and every commit confirmed an ancestor of HEAD via `git merge-base --is-ancestor`: `248fa35` (#603) metadata-ownership guard, `81b2148` (#652) bounds all 9 warm-daemon handlers, `e575075` (#604) lru_cache + #155's `path_provenance`, #140/#143 the `--` sentinel. No code change was needed. **Do NOT conflate the closed #143 sentinel item with #862** -- that is a separate, still-open nit at a different `agent_capsule.py` site. ORIGINAL TEXT FOLLOWS --  *(LOW)*
- [x] Dead code: delete `sidecar.py::_classify_lines` *(LOW)* — done 2026-08-01 backlog campaign, PR-D
- [x] apply_policy argv-sentinel — RETIRED (not fixed), 2026-08-01 backlog campaign, PR-D. See
      `docs/BACKLOG.md`'s LOW-severity section for the reasoning.

## UNSHIPPED ARTIFACTS -- found by a worktree sweep, 2026-08-02

Not "open work" someone chose; work that was **invisible** because its branch looked like a husk.
All three are now pushed to origin, so none is disk-only. `git merge-base --is-ancestor` says
"landed" for a branch whose COMMITTED head is on main -- it says nothing about uncommitted files in
the worktree, which is exactly how 519 lines hid behind an "ANCESTOR of main" verdict.

- [ ] **`perf/context-tests-limit-deadline` -- 519 lines, committed 2026-08-02 for PRESERVATION.**
  Another agent's opt10 campaign #3: `repo_map.py` +61 threading `_test_source_limit` through
  `build_symbol_impact/refs/callers_from_map`, which fed `ranked_files` to `_context_tests`
  UNBOUNDED (O(len(tests) * len(source_files)) via `_test_graph_score` rebuilding an aliases-by-file
  dict per test) while refs/callers consume only `test_matches[:1]` and discard the rest. Plus a
  452-line test file and a `--deadline` gate. **NOT verified: not run, not reviewed, base is
  #713-era.** `repo_map.py` is core -- needs a rebase, a real TDD gate and an observed red arm
  before it goes near a PR. Do NOT merge on the strength of the docstring.
- [ ] **`probe/classifier-feasibility` -- 1059 insertions across 34 files, never opened as a PR.**
  The `rrf_centrality` eval arm + `find_centrality_golden.jsonl` structural-centrality golden slice,
  built to de-risk #189 Item-2 (query-CONDITIONED centrality -- the ONE survivor after the other
  three CPU levers died on real data). Absent from `main` (verified: the eval harness on main has
  zero `centrality` hits, positive control 22 `def`s in the same file). **Needs a disposition:** did
  the probe reach a verdict? If it died like its siblings, record the negative and delete -- a dead
  lever nobody wrote down gets re-chased.
- [ ] **`rescue/lazy-wave-stash-2026-08-01` -- likely obsolete, kept deliberately.** The orphaned
  stash rescued during the parallel-worktree stash collision; a `temp-verify-preexisting-flake` hack
  on a **v1.64.2** base, ~38 releases stale. Almost certainly discardable, but it is the receipt for
  that incident. Delete once someone confirms nothing in it is wanted.

## BLOCKED — environment (not CEO-gated, just needs hardware)

- [ ] **#89** WSL `/mnt/c` absolute-path resolution in the native backend
- [ ] **#90** `tg scan` ast-grep Linux/WSL portability + doctor false-"available" exit-127
- [ ] **#109** CUDA GPU implicit-walk ceiling

## CEO-GATED (do not start without an explicit go)

- [ ] **#72** publish the benchmark proof-point (7.5× fewer tokens than grep) — public claim
- [ ] **#131 / #169** GPU deep-dive + multi-week rebuild; CUDA asset publishing is on a deliberate
  HOLD. Phase-0 shipped correctness-proven assets gated OFF by
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`; the flag-flip is the CEO's call.
- [ ] **#48** public-shim startup overhead — closed as an honest NEGATIVE (tg's native walk *is*
  rg's walk, same `ignore` crate, so widening relocates cost rather than removing it). The
  architectural remainder is a CEO scoping call.
- [ ] **#77** `tg ledger` local agent context-sharing — approved in principle, scope gated

---

## RETIRED (do not re-chase — each cost a real cycle to settle)

| Idea | Why it is dead |
|---|---|
| `HashSet<PathBuf>` distinct-path counter | The code it would edit documents the design as rejected: unbounded per-path Vec behind a mutex is a contention point AND a DoS surface (50k unreadable entries → 50k-entry payload). Also breaks byte-reproducibility. |
| Rename `incomplete_paths_count` | Its zero-cost precondition expired when the field shipped in v1.99.5; a published field is a 90-day dual-emit exercise, not a two-line diff. |
| `SearchStats::is_empty()` as a live bug | The guarded state is provably unreachable — every writer of `binary_match_files` is preceded by `searched_files += 1`. |
| cAST structural chunking | Real-corpus eval: net-wash quality, 24.4× slower. |
| Dense int8/binary/PCA embedding compression | Retired on measurement. |
| GPU-for-search crossover | Re-adjudicated 2026-07-21 across 10MB–5GB: no crossover at any scale; the shipped kernel is a position-parallel brute-force byte-compare, not PFAC. |
| "Beat rg on cold search" | Closed as an honest negative — tg's native walk IS rg's walk (same `ignore` crate). The campaign's return was a defect family, not milliseconds. |

---

## Reference

- Historical ledger: `docs/BACKLOG.md` · Contracts: `docs/CONTRACTS.md` · Laws: `AGENTS.md`
- Release mechanics + positioning rules: `.claude/skills/tensor-grep-release-and-positioning`
- What counts as proof: `.claude/skills/tensor-grep-validation-and-qa` (oracle forms 1–10)
