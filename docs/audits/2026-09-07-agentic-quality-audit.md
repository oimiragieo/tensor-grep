# Agentic quality, simplification, and integration audit — 2026-09-07

## Scope and result

Audited source: Git commit **5d67210e343f95f2bd4eac31d155f98b29b0c003**.
Installed CLI reported **1.118.0**. The repository virtual environment imports this checkout.
The working tree was clean before this documentation change.

Deliverables: this evidence report, the [implementation plan](../plans/2026-09-07-agentic-quality-simplification.md),
and the compact work ledger in [BACKLOG.md](../BACKLOG.md#agentic-quality-audit-2026-09-07).
There are **16 work packages: eight new AGT items and eight extensions to existing owners**.
A package can contain several related observations. These are not sixteen proven vulnerabilities.

The audit used tensor-grep search/inventory/orient/prepare, targeted source and test reads,
three independent read-only audit agents, and six Exa search queries plus a batched fetch.
No production code was changed, no models were downloaded or loaded, no full benchmark or test
matrix ran, and no GitHub comments, issues, PRs, merges, or publication actions were performed.
GitHub's open-PR query returned an empty list at the audit snapshot; this is not a CI-health claim.
The CLI interruption happened before documentation was written. Findings and source URLs were
recovered from the conversation; no original raw Exa response file is claimed.

**Classification:** C = concrete source-confirmed behavior; L = observed live probe;
S = structural simplification candidate; E = experiment requiring measurements.
Static race reasoning is not an executed concurrency reproduction. No speedup is claimed.
“READY” means the analyst can start the specified baseline/design work, not that a build or
security gate has approved a patch. Existing financial, publication, and platform gates remain.

## Findings and ownership

### 1. AGT-01 — Measure actual task success and confident wrong primary targets

**Priority P1; C.** The accuracy scorer accepts an expected alternative as a hit
(tests/eval/test_agent_accuracy.py:284). The workflow benchmark already measures hit@1,
hit@3, and false primary, but wrong_confident_miss uses NOT hit@3
(benchmarks/run_agent_workflow_benchmarks.py:488). Thus wrong-first/correct-second can
pass recall while escaping that particular high-confidence risk metric.
Partial results are annotated but can still count as hits in the accuracy gate (:333).

A real patch runner already applies patches and executes bounded validation
(benchmarks/run_patch_bakeoff.py:228, :366). Reuse it. The external patch-driver scorecard
instead aggregates compactness, command-name fit, and estimated parallel-read savings
(benchmarks/build_external_agent_patch_driver_scorecard.py:36, :72).
Those proxies must not be presented as measured solved-task success.
The inexpensive success harness deliberately uses a fixed rewrite and a filename-revealing
scenario (benchmarks/run_agent_success_harness.py:32, :102, :239).

**Action:** retain recall and smoke metrics, add primary-selection risk and complete-result
denominators, and join real patch correctness/cost records to the comparison report.
Use frozen external development/holdout splits. Existing A21/#72 own regression/publication
policy. Research: R1, R2, R7.

### 2. AGT-02 — Residual session refresh race and stale prepared decisions

**Priority P1; C, interleaving not executed.** Refresh reads an existing payload before locking
(src/tensor_grep/cli/session_store.py:813), copies last_prepare (:878), and only then locks
and publishes (:888). Concurrent session_prepare publishes under the same lock
(src/tensor_grep/cli/session_resume_service.py:35). Schedule:
refresh reads old decision -> prepare saves new decision -> refresh writes old decision.
The previous S4 “fixed” receipt therefore does not cover the full read-modify-write interval.

A separate gap: refreshing a map retains an earlier decision, while resume reports resumed:true
without the decision's originating generation (session_resume_service.py:49;
prepare_service.py:504). Session-map staleness checks do exist (session_store.py:983);
the finding concerns decision freshness AFTER a successful refresh.

**Action:** reopen only these residual S4 cases; preserve history with explicit generation and
freshness, merge the newest decision inside the publication lock, and Event-test both schedules.
Changed state/lock boundaries require independent adversarial review.

### 3. AGT-03 — Detected validators and machine next actions disagree

**Priority P1; C.** prepare_service.py:440 returns detected validation commands but :462
hardcodes the next successful action to uv run pytest -q for every repository.
test_prepare_next_action.py:13 verifies shape rather than language relevance.
The stop_if fields also need a defined lookup contract: a dotted nested field and a legacy
completeness field are currently mixed (:459).

Session prepare serializes a dataclass containing both named fields and the complete original
payload in raw (prepare_service.py:504, :537; session_resume_service.py:40). It also leaves
include_next_action at its false default. Cold and session consumers therefore navigate
different, duplicated representations.

**Action:** extend S3/S4 with a typed validation adapter, explicit stop-condition evaluation,
and versioned canonical serialization. Advice is not permission to execute repository scripts.
Never convert arbitrary shell text into trusted argv.

### 4. AGT-04 — Define and bound edit-ticket file population

**Priority P1; C.** edit_ticket_service.py:52 calls sorted(root.rglob("*")), then excludes
only .git and __pycache__ after traversal. :41 hashes complete files; build (:88) and verify
(:134) repeat the walk. The “tracked files” helper is not Git-tracked enumeration.
Dependency/build trees and tool-state writes can impose unnecessary work and scope failures.

**Action:** extend S1/S5's existing identity/atomicity work with an explicit protected population,
pre-descent pruning, streaming enumeration, and file/per-file/aggregate/deadline caps.
Tracked .github files and out-of-scope source additions must remain covered.
Do not solve latency by silently narrowing the safety boundary. Existing F5/F6 restrictions
are not removed. Exhaustion must never yield successful verification.

### 5. AGT-05 — Validate task intent and confidence before recommending an edit

**Priority P1; L/C.** The scoped live query below selected SearchResult rather than the inspected
backend-selection implementation Pipeline. Confidence rules are heuristic constants and
adjustments (agent_capsule_confidence.py:171, :276), not calibrated probabilities.
This is one counterexample, not a population accuracy estimate.

**Action:** pin existing rankings, add independently labeled intent/ambiguous/no-target tasks,
measure wrong-confident-primary plus abstention, and compare bounded changes to intent matching.
Do not hardcode this query or reduce every confidence score to make the risk metric pass.
Depends on AGT-01 measurement.

### 6. P9 extension — Reuse warm maps before building another daemon or index

**Priority P2; C/E.** session_resume_service.py:35 holds the root index lock while
build_prepare_snapshot runs; prepare_service.py:286 unconditionally rebuilds the map.
This warm rescan is already recorded under S4/P9. Contention magnitude is unmeasured.

core/semantic_index.py:12 explicitly says its persisted BM25 helpers are not CLI-wired.
The production caller search found no invocation; tests/unit/test_semantic_index.py exercises
them. The module uses two ordinary JSON writes (:80, :92) and a listed-path/mtime fingerprint
(:48). It is dormant library capability, not shipped CLI acceleration.

**Action:** reuse a freshness-validated map with generation-checked publication. Benchmark
before changing lock scope. Record whether the dormant index is retained, safely adopted, or
deprecated through an explicit public-API decision. Preserve CONTINUOUS-REFRESH/DD-006 gates.

### 7. P13 extension — Extract shared services, not just files

**Priority P2; S.** mcp_server.py:40 imports execution helpers from CLI main.py; examples live
at main.py:1034, :1095, :1463. mcp_symbol_tools.py:17 reaches back through the parent module,
and mcp_server.py:113 explains the monkeypatch compatibility coupling.
repo_map_output_budget.py:31 uses a similar late-binding proxy.

**Action:** first freeze actual import edges under existing P13, then extract one execution
service with explicit dependencies and retain compatibility wrappers. Migrate real CLI/MCP
consumers and patch seams together. Reconcile docs/design/2026-08-19-split-floor-escape.md;
do not erase its hard-won binding guarantees. No startup improvement is yet measured.

### 8. AGT-06 — Share vendored-root detection

**Priority P2; S.** bootstrap.py:1084 and main.py:2214 independently inspect top-level
directory children against the shared vendored-name policy. One returns a short-circuit
boolean; the other collects sorted names for diagnostics.

**Action:** share a lightweight iterator/probe, preserve each adapter's return shape and
short-circuit behavior, error handling, refusal decisions, and bootstrap import budget.
Do not merge argv parsing or import the full CLI into bootstrap.

### 9. AGT-07 — One internal completeness model, distinct public projections

**Priority P2; S.** cli/incompleteness.py:129 interprets legacy payload shapes for MCP;
main.py:7060 separately identifies scan incompleteness and explicitly excludes display caps.
main.py:7082 builds another result view.

**Action:** encode scan completeness, output completeness, causes, and retry suitability
separately. Project into existing CLI/MCP fields without changing exits or deleting legacy
keys. Output-cap-only is not automatically a failed scan; permissions are not budget-remediable.
P3 stays shipped; this is consolidation, not reopening its completed feature.

### 10. MCP-SURFACE extension — Progressive disclosure and typed wire results

**Priority P2; C/E.** tg_query has a broad action-dependent signature and returns str
(mcp_server.py:4575); other meta-tools follow that JSON-string pattern. Existing caps,
meta-tools, and output-budget helpers already exist and must be reused.
orient's snippet budget does not constrain its entire serialized symbol map; the live
orientation call reported truncation and an estimate exceeding the requested snippet budget.
That is a documented distinction, not proof of a broken existing budget contract.

**Action:** inventory actual negotiated MCP wire output before alleging missing structuredContent;
prototype an opt-in complete-response byte/token budget, concise/detailed views, and bounded
follow-up references. Add typed output only with SDK/client negotiation and legacy compatibility.
Do not silently switch protocol versions or claim tool-result pagination is mandated by
tools/list pagination. Existing Task 4 and MCP-LEAN-DEFAULT gates apply. Research: R1, R3, R4.

### 11. AGT-08 — Explain actual ranking contributions

**Priority P2; C.** reranker.py:475 builds why-ranked explanations from matched lexical
terms and line spans; main.py:1733 uses it after ranking. test_find_why_ranked.py:27
checks list type rather than whether an explanation accounts for the ranking.
This is the existing S6 follow-up given a stable actionable owner.

**Action:** carry BM25/dense/path/fusion/re-rank contributions and fallback state through the
ranking result. Preserve ranking order and disclose absent contributors. The fusion helper's
default is max, not conventional sum (retrieval_fusion.py:35); explain the actual configured
operation. Research: R1.

### 12. P12 correction — Profile static embeddings before choosing ONNX/int8

**Priority P2; C/E.** retrieval_dense.py:93 loads StaticModel; DenseIndex encodes corpus
chunks (:175) and query scores are fully sorted (:206). main.py:1653 constructs the index.
Model2Vec's primary documentation describes token-vector lookup and averaging, not an
ordinary transformer inference graph.

**Action:** measure process startup, model load, chunking, corpus encode, query encode, scoring,
and top-k selection separately. Compare reuse/batching first. Quantization or ONNX is an
optional measured branch with quality/portability controls, not a predetermined solution.
The old 80ms -> 10ms/AVX-512 proposal is a target/hypothesis, not verified baseline. Research: R6.

### 13. P14 extension — Honest per-fact provenance and bounded precise navigation

**Priority P2; C/E.** lang_registry.py:72 already distinguishes parsed/missing provenance;
repo_map.py:881 describes symbol-navigation limits. lsp_external_provider.py:350 already
provides a provider client. Cross-file text-prefilter limits remain documented
(repo_map.py:3759).

**Action:** reuse these seams to attach provider, extraction mode, revision/content identity,
and independent freshness status to facts. Prototype one language's precise reference
confirmation through the existing provider. Consider SCIP import only if that measured
pilot shows a useful gap; do not start a competing universal language server.
Serena and Sourcegraph already offer precise semantic navigation, so the old P14
“entire market lacks this / first-to-market” claim is not established. Research: R4, R5.

### 14. P7 correction — AST caches already exist

**Priority P2; C/E.** ast_backend.py:150 stores shared parsers, queries, parsed sources,
and node indexes; :347 loads persisted results, :487 loads persisted node indexes.

**Action:** census caches by actual route, language, flags, and lifetime. Measure which missing
reuse matters before adding a cache. Treat a no-new-cache conclusion as successful research.
P7's <5ms aspiration needs a named workload, warm state, hardware, and process boundary.

### 15. P10 extension — Evidence of execution is not evidence of symbol coverage

**Priority P2; C/E.** evidence_receipt.py:751 composes receipts from existing artifacts;
evidence_signing.py:358 signs receipts. The real patch runner already executes validation.
These are starting points, not absent functionality.

**Action:** bind any executed validation to revision, diff, argv, cwd, exits, collected tests,
and bounded output. Distinguish a test associated with a symbol from instrumentation proving
coverage of that symbol. A valid signature proves integrity relative to a trusted key, not
that a self-report is true. Preserve existing signer/identity/platform gates. Research: R2, R7.

### 16. P15 extension — Generate capability facts and retire unsupported prose

**Priority P2; C.** README.md:29 says no subprocess overhead, while native main.rs:1594
and python_sidecar.rs implement delegation. README.md's scan-limit paragraph still cites
512 while repo_map.py:477 defines 2000. Existing language-table drift and P15 already own
generation work.

**Action:** generate registry/capability tables and separately review positioning prose.
State native/Python entry differences, optional dependencies, scope/fallbacks, and cache
lifetime. Remove unsupported universal/first-to-market claims rather than replacing them
with new unverifiable superlatives. P4 remains closed.

## Live probes retained in the conversation

These were diagnostic observations, not latency benchmarks. They used the real local
Python front door via the repository virtual environment, with an outer 25-second subprocess
timeout and a 12-second CLI deadline. Query: “select backend for regex search”.

| Scope | Exit | Primary target | Confidence | Ask required | Partial |
|---|---:|---|---:|---|---|
| src/tensor_grep/core | 0 | core/result.py :: SearchResult | 0.94 | false | field absent |
| src/tensor_grep | 2 | backends/cpu_backend.py :: search | 0.75 | false | true |

The full-scope result's exit 2/partial flag is a real warning; it must not be counted as
complete readiness evidence just because the ask flag is false. No root cause for its
specific ranking is claimed. The scoped result's cited implementation is core/pipeline.py:132.
Paths above are normalized to repo-relative form; raw user-machine paths are not published.

Structural inventory: 126 tracked Python source files, 22 tracked Rust source files, and
463 tracked Python test files. Large modules at the inspected commit:
cli/main.py 13,523 lines; cli/repo_map.py 15,210; cli/mcp_server.py 5,679;
rust_core/src/main.rs 15,117. File size identifies coupling risk, not a defect by itself.

## Exa research register

Retrieved through Exa on 2026-09-07. Sources are primary documentation or first-party project
repositories. These establish design ideas and competitor capabilities, not tensor-grep wins.
Publication dates are given only where returned; living documentation can change.

| ID | Primary source | Supported observation | Applied to |
|---|---|---|---|
| R1 | [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) (2025-09-11) | Task-shaped tools, meaningful responses, evaluation of tool calls/tokens/errors | AGT-01, AGT-08, MCP-SURFACE |
| R2 | [Anthropic: Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) (2026-01-09) | Match graders to verifiable outcomes and inspect transcripts as a separate signal | AGT-01, P10 |
| R3 | [MCP tools, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) and [2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | Versioned schemas, structured content, resource links; supported semantics depend on negotiated revision | MCP-SURFACE |
| R4 | [Aider repository maps](https://aider.chat/docs/repomap.html) | Graph-ranked relevant symbols under a context budget | AGT-05, MCP-SURFACE |
| R5 | [Serena](https://github.com/oraios/serena), [Sourcegraph precise navigation](https://sourcegraph.com/docs/code-navigation/precise-code-navigation), [SCIP](https://github.com/scip-code/scip) | Symbol-aware tools and precise indexes/providers are existing competitor capabilities | P14 |
| R6 | [Model2Vec documentation](https://minish.ai/packages/model2vec/introduction/) and [implementation](https://github.com/MinishLab/model2vec) | Static token-vector lookup/averaging; quantization is an option | P12 |
| R7 | [SWE-bench Verified](https://www.swebench.com/verified) | Human-reviewed tasks and controlled harness versions aid interpretable comparisons | AGT-01, P10 |

Research synthesis: pursue fewer wrong autonomous edits, smaller sufficient context, faster
unchanged warm turns, and inspectable evidence. “World's best” is a measurable ambition,
not a conclusion of this audit. The plan defines matched-task comparisons and refusal to
publish superiority without complete evidence and the existing #72 decision.

## Coverage and limitations

Sampled production Python/Rust routes, source/test seams, existing benchmark/evaluation code,
and current backlog ownership. This was not an exhaustive function census, exploit audit,
full language correctness evaluation, or release verification. No claimed dead public API is
authorized for deletion. Static findings require the proposed control/reproduction before fixes;
refuted findings should be retired with evidence and no forced code change.

Available independent agents reviewed simplification, agent quality, and evaluation.
Fable/Opus-specific approval and a formal design council were not performed in this session.
The delivered plan is concrete review material; it must not be represented as security-cleared.

## Documentation validation

The new plan's 16 task anchors match all 16 backlog packages; the 15 newly indexed owners plus
the existing MCP-SURFACE owner are present in the canonical board. The board now has 44 rows:
7 READY, 14 BLOCKED, 4 CEO_GATED, 6 DEMAND_GATED, 8 SHIPPED and 5 RETIRED.
The tracker test's expected population was extended; no application code changed.

Six selected tracker/governance checks passed, with 38 unrelated tests deselected. The initial
pytest invocation could not accept --timeout because pytest-timeout is absent in this environment.
The successful documentation-only checks ran under a 30-second outer watchdog; no product or
concurrency tests were substituted for the missing plugin. Ruff check and format check passed for
the modified tracker test. Governance size and whitespace checks passed. New referenced paths in
the plan were checked: absent paths are explicitly proposed Create artifacts, not existing helpers.

A separate available agent reviewed the completed plan and found one must-fix: its outer Windows
watchdog supervised uv rather than pytest. The runner now starts its supervisor through uv and
directly supervises the same interpreter's pytest process, with explicit separate child-cleanup
requirements and no process-tree-containment claim. No other must-fix was reported by that review;
this is not a substitute for the future formal design/security gates.
