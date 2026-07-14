# Harness API

`tg.exe` exposes a small set of machine-readable output shapes for harnesses and agents. This document describes the current v1 JSON contracts emitted by the native Rust CLI, plus the current GPU sidecar hybrid shape. `tg.exe search --format rg --json ...` is deliberately not a tensor-grep envelope; it streams ripgrep JSON Lines events for tools that require rg's event schema.

All committed examples live in [`docs/examples/`](examples/) and are valid single-document JSON files generated from real `tg.exe` commands against temporary fixtures created under `bench_data/`.

> `bench_data/*.log` is ignored by default because of the repo ignore rules, so search examples that target log files use `--no-ignore`.

## Common envelope fields

These top-level fields are shared across every JSON shape documented here.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | `integer` | Contract version. Current value: `1`. |
| `routing_backend` | `string` | Backend selected by the Rust control plane, such as `CpuBackend`, `TrigramIndex`, `AstBackend`, or `GpuSidecar`. |
| `routing_reason` | `string` | Stable reason string describing why that backend was chosen. |
| `sidecar_used` | `boolean` | `true` only when the Rust CLI delegated the request through the Python sidecar. |

## Example files

| Shape | Trigger | Example |
| --- | --- | --- |
| Search JSON | `tg.exe search --json ...` | [`examples/search.json`](examples/search.json) |
| Ripgrep JSON Lines | `tg.exe search --format rg --json ...` | Raw rg event stream; use ripgrep's JSON Lines contract. |
| AST Run JSON | `tg.exe run --lang <lang> --json ...` | [`examples/ast_run.json`](examples/ast_run.json) |
| Index search JSON | `tg.exe search --index --json ...` | [`examples/index_search.json`](examples/index_search.json) |
| Rulesets JSON | `tg.exe rulesets --json` | [`examples/rulesets.json`](examples/rulesets.json) |
| Ruleset scan JSON | `tg.exe scan --ruleset <name> --json ...` | [`examples/ruleset_scan.json`](examples/ruleset_scan.json) |
| Repo map JSON | `tg.exe map --json ...` | [`examples/repo_map.json`](examples/repo_map.json) |
| Context pack JSON | `tg.exe context <path> <query> --json` | [`examples/context_pack.json`](examples/context_pack.json) |
| Edit plan JSON | `tg.exe edit-plan <path> <query> --json` | [`examples/edit_plan.json`](examples/edit_plan.json) |
| Context render JSON | `tg.exe context-render <path> <query> --json` | [`examples/context_render.json`](examples/context_render.json) |
| Agent capsule JSON | `tg.exe agent <path> <query> --json` | Schema documented in Agent Capsule JSON below. |
| Rewrite plan JSON | `tg.exe run --rewrite ...` | [`examples/rewrite_plan.json`](examples/rewrite_plan.json) |
| Apply + verify JSON | `tg.exe run --rewrite ... --apply --verify --json ...` | [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json) |
| Attempt ledger JSON | multi-attempt harness/replay ledger | [`examples/attempt_ledger.json`](examples/attempt_ledger.json) |
| Multi-session attempt ledger JSON | multi-session replay and handoff ledger | [`examples/multi_session_attempt_ledger.json`](examples/multi_session_attempt_ledger.json) |
| Multi-task attempt ledger JSON | multi-task replay chain ledger | [`examples/multi_task_attempt_ledger.json`](examples/multi_task_attempt_ledger.json) |
| Audit manifest verify JSON | `tg.exe audit-verify <manifest> --json` | [`examples/audit_manifest_verify.json`](examples/audit_manifest_verify.json) |
| GPU CPU fallback JSON | `tg.exe search --gpu-device-ids ... --json ...` on a CPU fallback route | [`examples/gpu_cpu_fallback_search.json`](examples/gpu_cpu_fallback_search.json) |
| GPU sidecar JSON | `tg.exe search --gpu-device-ids ... --json ...` | [`examples/gpu_sidecar_search.json`](examples/gpu_sidecar_search.json) |
| Calibrate JSON | `tg.exe calibrate` | [`examples/calibrate.json`](examples/calibrate.json) |
| Search NDJSON | `tg.exe search --ndjson ...` | [`examples/search.ndjson`](examples/search.ndjson) |
| Symbol defs JSON | `tg.exe defs <path> <symbol> --json` | [`examples/defs.json`](examples/defs.json) |
| Symbol source JSON | `tg.exe source <path> <symbol> --json` | [`examples/source.json`](examples/source.json) |
| Symbol impact JSON | `tg.exe impact <path> <symbol> --json` | [`examples/impact.json`](examples/impact.json) |
| Symbol refs JSON | `tg.exe refs <path> <symbol> --json` | [`examples/refs.json`](examples/refs.json) |
| Symbol callers JSON | `tg.exe callers <path> <symbol> --json` | [`examples/callers.json`](examples/callers.json) |
| Symbol blast radius JSON | `tg.exe blast-radius <path> <symbol> --json` | [`examples/blast_radius.json`](examples/blast_radius.json) |
| Symbol blast radius plan JSON | `tg.exe blast-radius-plan <path> <symbol> --json` | [`examples/blast_radius_plan.json`](examples/blast_radius_plan.json) |
| Symbol blast radius render JSON | `tg.exe blast-radius-render <path> <symbol> --json` | [`examples/blast_radius_render.json`](examples/blast_radius_render.json) |
| Session open JSON | `tg.exe session open ... --json` | [`examples/session_open.json`](examples/session_open.json) |
| Session context JSON | `tg.exe session context <id> <path> <query> --json` | [`examples/session_context.json`](examples/session_context.json) |
| Doctor JSON | `tg.exe doctor --json` | Operational diagnostics schema documented below. |
| MCP rewrite diff JSON | `tg_rewrite_diff(...)` | [`examples/mcp_rewrite_diff.json`](examples/mcp_rewrite_diff.json) |

## Search JSON

Emitted by native text search when `--json` is set.

Example: [`examples/search.json`](examples/search.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Always `1` for the current contract. |
| `routing_backend` | `string` | `CpuBackend` for the committed example. |
| `routing_reason` | `string` | `cpu-native` for the committed example. |
| `sidecar_used` | `boolean` | `false` for native CPU search. |
| `requested_gpu_device_ids` | `array<integer>` | Explicit GPU IDs requested by the caller; empty for CPU search. |
| `routing_gpu_device_ids` | `array<integer>` | GPU IDs actually used by the runtime route. Empty for native CPU, index, and CPU fallback routes. |
| `query` | `string` | Search pattern exactly as passed on the command line. |
| `path` | `string` | Search root passed to the command. |
| `total_matches` | `integer` | Number of materialized matches in `matches`. |
| `matches` | `array<object>` | Match rows. |

Each `matches[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute path to the matching file. |
| `line` | `integer` | 1-based line number. |
| `text` | `string` | Full matching line text. |

## AST Run JSON

Emitted by native AST search when `tg.exe run --json` is set without `--rewrite`.

Example: [`examples/ast_run.json`](examples/ast_run.json)

The top-level envelope matches Search JSON and keeps the same `query`, `path`, `total_matches`, and `matches[]` fields.

Each `matches[]` object keeps the standard search fields and additionally includes:

| Field | Type | Notes |
| --- | --- | --- |
| `range` | `object` | Zero-based AST span metadata with `byteOffset`, `start`, and `end`. |
| `range.byteOffset` | `object` | Byte offsets with `start` and `end` exclusive. |
| `range.start` | `object` | Zero-based `line` and `column` for the match start. |
| `range.end` | `object` | Zero-based `line` and `column` for the match end. |
| `metaVariables` | `object` | Captured AST metavariables from the native matcher. |
| `metaVariables.single` | `object` | Single captures keyed by metavariable name. |
| `metaVariables.multi` | `object` | Multi captures keyed by metavariable name, each value an ordered array of captures. |

Each `metaVariables.single.<name>` or `metaVariables.multi[]` capture object uses:

| Field | Type | Notes |
| --- | --- | --- |
| `text` | `string` | Exact captured source text. |
| `range` | `object` | Same zero-based span shape used by the parent match. |

## Index Search JSON

Emitted by native trigram index search with `tg.exe search --index --json ...`.

Example: [`examples/index_search.json`](examples/index_search.json)

The shape matches Search JSON exactly; only the routing envelope changes.

| Field | Type | Notes |
| --- | --- | --- |
| `routing_backend` | `string` | `TrigramIndex` in the example. |
| `routing_reason` | `string` | `index-accelerated` in the example. |
| `sidecar_used` | `boolean` | Always `false` for the native index path. |
| `routing_gpu_device_ids` | `array<integer>` | Empty for the native index path. |
| `query` | `string` | Original literal/regex query. |
| `path` | `string` | Indexed search root. |
| `total_matches` | `integer` | Number of returned index matches. |
| `matches[].file` | `string` | Absolute file path. |
| `matches[].line` | `integer` | 1-based line number. |
| `matches[].text` | `string` | Matching line text. |

## Doctor JSON

Emitted by `tg.exe doctor --json` and intended for operational automation.

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | `integer` | Doctor JSON schema version. Current value: `2`. |
| `doctor_schema_version` | `integer` | Alias for the doctor schema version for consumers that avoid generic `schema_version` names. |
| `lsp` | `object` | LSP diagnostic envelope. Present even when LSP probing is disabled. |
| `lsp.schema_version` | `integer` | LSP diagnostic sub-schema version. Current value: `2`. |
| `lsp.providers` | `array<object>` | Provider status rows in deterministic provider order. |
| `lsp.providers_by_language` | `object` | Keyed compatibility map by language. Entries mirror provider rows and include `health` as an alias for `health_status`. |
| `lsp_provider_items` | `array<object>` | Top-level alias for `lsp.providers` for array-oriented consumers. |
| `lsp_providers` | `object` | Top-level keyed compatibility map matching `lsp.providers_by_language`. |

Consumers should prefer the schema-versioned `lsp.providers` array for iteration and the keyed maps only for direct language lookup or backward compatibility with the pre-array doctor shape.

## Rulesets JSON

Emitted by `tg.exe rulesets --json`.

Example: [`examples/rulesets.json`](examples/rulesets.json)

Use this shape when a harness needs to discover the built-in security or compliance packs before choosing a scan.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `builtin-rulesets`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `rulesets` | `array<object>` | Registered built-in packs. |

Each `rulesets[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `string` | Stable built-in ruleset name. |
| `description` | `string` | Human-readable summary. |
| `category` | `string` | Current built-in packs use `security`. |
| `status` | `string` | Lifecycle label such as `preview`. |
| `default_language` | `string` | Default language used if the caller does not override it. |
| `languages` | `array<string>` | Supported language identifiers. |
| `rule_count` | `integer` | Total number of rules registered across the supported languages. |

## Ruleset Scan JSON

Emitted by `tg.exe scan --ruleset <name> --json ...`.

Example: [`examples/ruleset_scan.json`](examples/ruleset_scan.json)

Use this shape when a harness wants structured findings from a built-in security or compliance pack.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `builtin-ruleset-scan` for a built-in pack, or `ast-inline-rules-scan` for `--inline-rules`/`inline_rules` (see "Inline Rules" below). |
| `sidecar_used` | `boolean` | Always `false`. |
| `config_path` | `string` | Built-in config reference such as `builtin:crypto-safe`, the literal string `inline-rules` for an inline-rules scan, or the resolved rule/config file path for the other CLI-only sources. |
| `path` | `string` | Scan root. |
| `ruleset` | `string \| null` | Selected built-in ruleset name, or `null` for an inline-rules scan. |
| `language` | `string` | Effective language used to resolve the rule set. |
| `rule_count` | `integer` | Total rules executed. |
| `matched_rules` | `integer` | Number of rules that matched at least once. |
| `total_matches` | `integer` | Aggregate matches across every rule. |
| `backends` | `array<string>` | Backends used during the scan. |
| `findings` | `array<object>` | Stable per-rule finding summaries. |

### Inline Rules

`tg.exe scan --inline-rules <yaml>` (CLI) and `tg_ruleset_scan(inline_rules=<yaml>, ...)` (MCP)
run one or more `---`-separated ast-grep rule YAML documents supplied directly as a string --
no built-in pack, no rule file, no sgconfig, zero file I/O for the rule source itself. Exactly
one of `--ruleset`/`ruleset` or `--inline-rules`/`inline_rules` is required; supplying both, or
neither, is a fail-closed `invalid_input` error (`ruleset and inline_rules are mutually
exclusive.` / `Exactly one of ruleset or inline_rules is required.`).

Each document may set `id`, `language` (falls back to the tool's `language`/`--language`
override, then `python`), `severity`, `message`, and either a top-level `rule.pattern` (or
bare `pattern`), or a `rules: [...]` list of the same shape for multiple rules in one document:

```yaml
id: no-print
language: python
severity: warning
message: Avoid print in library code.
rule:
  pattern: print($A)
```

Invalid YAML, a document with no extractable pattern, or an unsupported language all fail
closed with a structured `invalid_input` error (never a raw traceback). The MCP tool
additionally bounds the raw `inline_rules` string to 64KiB (`_MAX_INLINE_RULES_CHARS` in
`mcp_server.py`) before it ever reaches the YAML loader -- PyYAML's `SafeLoader` still resolves
anchors/aliases, so an unbounded string is a "billion laughs"-style expansion-bomb DoS surface
on an (LLM-influenceable) MCP tool call even though it cannot execute arbitrary code.

The CLI additionally supports `--rule <file>` (a single rule file, no sgconfig required) and
the full `--config sgconfig.yml` project-config workflow; neither is exposed over MCP yet
(`--rule` is a deferred follow-up; `--config` is deferred because its `ruleDirs`/`testDirs` do
an **unconfined** recursive directory walk that confining only the top-level scan root would
not close).

Each `findings[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `rule_id` | `string` | Stable built-in rule identifier. |
| `language` | `string` | Rule language. |
| `severity` | `string` | Built-in severity label. |
| `message` | `string` | Remediation guidance for the rule. |
| `fingerprint` | `string` | Deterministic SHA-256 fingerprint derived from the rule identity and matched file set. |
| `status` | `string` | Optional finding lifecycle state: `new`, `existing`, `suppressed`, or `clear` when baseline/suppression controls are enabled. |
| `matches` | `integer` | Match count produced by the rule. |
| `files` | `array<string>` | Stable list of files matched by the rule. |
| `evidence` | `array<object>` | Stable per-file evidence rows with `file`, `match_count`, and optional bounded `snippets[]` when snippet evidence is enabled. |

Optional top-level baseline fields:

| Field | Type | Notes |
| --- | --- | --- |
| `baseline` | `object` | Present when `--baseline` is used. Includes `path`, `new_findings`, `existing_findings`, `resolved_findings`, and `resolved_fingerprints`. |
| `baseline_written` | `object` | Present when `--write-baseline` is used. Includes the output `path`, written `fingerprints`, and `count`. |
| `suppressions` | `object` | Present when `--suppressions` is used. Includes the suppression file `path` and `suppressed_findings`. |
| `suppressions_written` | `object` | Present when `--write-suppressions` is used. Includes the written suppression file `path`, `fingerprints`, and `count`. |

## Orient Capsule JSON

Emitted by `tg.exe orient <path> --json` and `tg_orient(...)` (MCP). Call this FIRST when
orienting on an unfamiliar repo -- it answers "what is this codebase and where do I start" in
one bounded call, cheaper than a full `tg_repo_map`/`tg_context_pack` walk.

Built by `build_orient_capsule_json` (`orient_capsule.py`); unlike most other JSON shapes in
this document it does **not** carry the common envelope fields (`version`/`routing_backend`/
`sidecar_used`) -- only `routing_reason`. The MCP tool still injects `mcp_contract_version` and
`schema_version` via the same `_inject_mcp_contract_fields` wrapper every tool uses.

| Field | Type | Notes |
| --- | --- | --- |
| `routing_reason` | `string` | Always `orient`. |
| `path` | `string` | Absolute root path oriented on. |
| `central_files` | `array<object>` | Top files ranked by import-graph in-degree centrality, each `{file, graph_score}`. |
| `entry_points` | `array<object>` | Heuristically detected entry points, each `{file, reason}`. |
| `symbol_map` | `object` | Top symbols per central file, keyed by file path; each entry an array of `{name, kind, line}` (bounded to 8 per file). |
| `snippets` | `array<object>` | Bounded AST-boundary source snippets for the highest-ranked central files, each `{file, source, truncated}`. |
| `token_estimate` | `integer` | Heuristic token estimate (`len/3.5`) for the full rendered capsule. |
| `token_budget_label` | `string` | Human-readable summary of `token_estimate` and the snippet token budget. |
| `truncated` | `boolean` | `true` when the snippet token budget clipped or dropped a snippet. |
| `scan_limit` | `integer` | The effective `max_repo_files` used to build the underlying repo map. |
| `suggested_scope` | `object \| null` | Additive (audit #93 SUB-2). Present only when the underlying repo scan itself was truncated (distinct from `truncated` above, which is snippet/token-budget only) AND a centrality-weighted directory rollup found a clear winner: `{dirs: [absolute_path], confidence: "heuristic"}`. `null` on a complete scan, or when the top two directories are tied/near-tied (degrade rather than guess). |

`ignore` (repeatable glob, matches basename or repo-relative path) excludes a subtree from the
**centrality ranking** only -- the files are still walked, just kept out of the central-files/
snippet selection. Useful for keeping a vendor or skill-code tree from crowding out the real
architecture hubs on a harness or monorepo.

## Repo Map JSON

Emitted by `tg.exe map --json ...`.

Example: [`examples/repo_map.json`](examples/repo_map.json)

Use this shape when an agent needs a deterministic repository inventory before choosing edits.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `repo-map`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `coverage` | `object` | Self-description for the current inventory/navigation coverage. |
| `path` | `string` | Absolute root path inventoried. |
| `files` | `array<string>` | Non-test files included in the inventory. If the requested root only contains tests, this falls back to the test files so agent inventory is not empty. |
| `symbols` | `array<object>` | Deterministic symbol inventory. |
| `imports` | `array<object>` | Per-file import inventory. |
| `tests` | `array<string>` | Test files associated with the inventory root. |
| `related_paths` | `array<string>` | Stable union of relevant source and test paths. |
| `scan_limit` | `object` | Optional; present when the inventory was built with `--max-repo-files` / `max_repo_files`. Includes `max_repo_files`, `scanned_files`, and `possibly_truncated`. |

Each `symbols[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `string` | Symbol name. |
| `kind` | `string` | Current values include `class` and `function`. |
| `file` | `string` | Absolute file path containing the symbol. |
| `line` | `integer` | 1-based line number. |

Each `imports[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute file path. |
| `imports` | `array<string>` | Imported module names extracted from the file. |

Current `coverage` values:

| Field | Type | Notes |
| --- | --- | --- |
| `language_scope` | `string` | Currently `python-js-ts-rust`. |
| `symbol_navigation` | `string` | Currently `python-ast+parser-js-ts-rust`. |
| `test_matching` | `string` | Currently `filename+import+graph-heuristic`. |

## Context Pack JSON

Emitted by `tg.exe context <path> <query> --json`.

Example: [`examples/context_pack.json`](examples/context_pack.json)

Use this shape when an agent needs a query-driven subset of the repository map before choosing edits.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `context-pack`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `query` | `string` | Query text used for ranking. |
| `path` | `string` | Absolute root path inventoried. |
| `files` | `array<string>` | Ranked source files related to the query. |
| `file_matches` | `array<object>` | Ranked source file metadata with stable `path`, `score`, and `reasons`. |
| `file_summaries` | `array<object>` | Compact top-level symbol skeletons for the ranked files. |
| `symbols` | `array<object>` | Ranked symbols related to the query. |
| `imports` | `array<object>` | Ranked import rows related to the query. |
| `tests` | `array<string>` | Ranked test files related to the query. |
| `test_matches` | `array<object>` | Ranked test metadata with stable `path`, `score`, and `reasons`. |
| `related_paths` | `array<string>` | Stable merged order of the highest-value source and test paths. |
| `ranking_quality` | `string` | Actionable ranking strength label: `strong`, `moderate`, or `weak`. |
| `coverage_summary` | `object` | Query-time trust summary covering parser-backed fields, heuristic fields, and graph completeness. |

The `coverage_summary` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `evidence_counts` | `object` | Query-level counts for `parser_backed`, `graph_derived`, and `heuristic` evidence observed in the ranked payload. |
| `evidence_ratios` | `object` | Query-level normalized ratios for the same evidence classes, useful when result sizes differ across queries. |

Each ranked `symbols[]` object extends the Repo Map JSON symbol shape with:

| Field | Type | Notes |
| --- | --- | --- |
| `score` | `integer` | Deterministic query relevance score. |

Each `file_matches[]` and `test_matches[]` object uses:

| Field | Type | Notes |
| --- | --- | --- |
| `path` | `string` | Absolute path for the ranked file. |
| `score` | `integer` | Deterministic rank score used for ordering. |
| `graph_score` | `number` | Optional personalized reverse-import score when graph ranking contributes to file selection. |
| `reasons` | `array<string>` | Stable provenance labels such as `path`, `symbol`, `definition`, `import`, `import-graph`, `graph-centrality`, `filename`, or `test-graph`. |
| `provenance` | `array<string>` | Normalized trust labels such as `parser-backed`, `graph-derived`, `filename-convention`, or `heuristic`. |

Each `test_matches[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `association` | `object` | Test-association trust metadata with `edge_kind`, `confidence`, and normalized provenance labels. |

Each `file_summaries[]` object uses:

| Field | Type | Notes |
| --- | --- | --- |
| `path` | `string` | Absolute path for the summarized file. |
| `symbols` | `array<object>` | Ordered top-level symbol skeletons with `name`, `kind`, and `line`. |

Each ranked `imports[]` object extends the Repo Map JSON import shape with:

| Field | Type | Notes |
| --- | --- | --- |
| `score` | `integer` | Deterministic query relevance score. |
| `provenance` | `string` | Import-source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

## Edit Plan JSON

Emitted by `tg.exe edit-plan <path> <query> --json`.

Example: [`examples/edit_plan.json`](examples/edit_plan.json)

Use this shape when an agent wants ranked edit targets and plan recommendations without the rendered prompt bundle.

It reuses the Context Pack JSON shape and adds:

| Field | Type | Notes |
| --- | --- | --- |
| `routing_reason` | `string` | `context-edit-plan` for direct builds, or `session-context-edit-plan` when top-level native-provider CLI requests reuse an already-running daemon. |
| `max_files` | `integer` | Maximum files retained in the plan payload. |
| `max_sources` | `integer` | Maximum related source/span records retained in `edit_plan_seed` and candidate edit spans. |
| `max_tokens` | `integer|null` | Accepted for agent command-surface parity with `context-render` and `agent`; edit-plan emits no rendered source text. |
| `max_symbols` | `integer` | Maximum ranked symbols retained in the plan payload. |
| `semantic_provider` | `string` | Effective semantic provider used for primary-target proof. Defaults to `native`; explicit `lsp` / `hybrid` modes may add row-level LSP proof fields only when a completed provider request contributed evidence. |
| `candidate_edit_targets` | `object` | Highest-value files, symbols, tests, and ranked span anchors carried forward for downstream edit planning. |
| `edit_plan_seed` | `object` | Primary file/symbol/span, related spans, suggested edits, dependent files, edit ordering, structured validation plan, validation commands, and rollback risk. |
| `navigation_pack` | `object` | Compact AI-facing navigation bundle with the primary target, mention-ready follow-up reads, related tests, and validation commands. |
| `validation_commands` | `array<string>` | Top-level copy of the best validation commands from `navigation_pack` or `edit_plan_seed` for quick agent access. Matches the `context-render` contract. |
| `validation_plan` | `array<object>` | Top-level copy of the structured validation plan steps (`command`, `confidence`, `detection`, `runner`, `scope`, optional `target`) from `edit_plan_seed.validation_plan`, for quick agent access without walking into `edit_plan_seed`. Matches the `agent` capsule contract's top-level `validation_plan` shape. |
| `confidence` | `object` | Overall score and downgrade reasons, matching the `agent` capsule contract's top-level `confidence` shape (`overall`, `downgrade_reasons`). Computed via the same trust-check and confidence ladder `agent` uses -- see the Agent Capsule JSON `confidence` row below and `docs/CONTRACTS.md`'s edit-planning contracts for exactly which agent-only render/call-site-evidence enrichments are (honestly) never applied here. |
| `ask_user_before_editing` | `object` | `required` plus reasons, matching the `agent` capsule contract's top-level `ask_user_before_editing` shape. Set when validation-command evidence is absent, confidence is below 0.75, the underlying repo scan was truncated, or a trust-check downgrade fired (query-language mismatch, unaligned validation evidence). |

When this payload is carried into `python benchmarks/run_tensor_grep_patch_driver.py`, the emitted patch-driver records preserve both `edit_plan_seed` and `navigation_pack` so executor loops can keep the richer plan and the smaller planner-to-reader handoff together.

Each ranked `candidate_edit_targets.spans[]` or `edit_plan_seed.related_spans[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `array<string>` | Normalized trust labels for the span ranking inputs. |
| `rationale` | `string` | Deterministic span-selection explanation derived from the ranking reasons and graph depth. |

Each `edit_plan_seed.suggested_edits[]` object uses:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute path for the recommended follow-up edit. |
| `symbol` | `string` | Symbol or enclosing region to edit. |
| `start_line` | `integer` | Recommended start line for the edit. |
| `end_line` | `integer` | Recommended end line for the edit. |
| `edit_kind` | `string` | Stable label such as `caller-update` or `dependency-update`. |
| `rationale` | `string` | Deterministic one-line explanation for the recommendation. |
| `confidence` | `number` | Confidence score in the recommended edit target. |

`navigation_pack` currently includes:

- `primary_target`
- `follow_up_reads`
- `parallel_read_groups`
- `related_tests`
- `validation_commands`
- `edit_ordering`
- `rollback_risk`

`parallel_read_groups` is the deterministic fan-out plan for downstream agent loops. The current contract uses three ordered phases:

- phase `0`: `primary`
- phase `1`: `related`
- phase `2`: `test`

Each group uses:

| Field | Type | Notes |
| --- | --- | --- |
| `phase` | `integer` | Ordered read phase; lower phases should run first. |
| `label` | `string` | Stable group name: `primary`, `related`, or `test`. |
| `can_parallelize` | `boolean` | Whether the reads in the group are safe to fan out in parallel. |
| `mentions` | `array<string>` | Mention-ready refs for the ranges in this phase. |
| `files` | `array<string>` | Absolute file paths represented in this phase. |
| `roles` | `array<string>` | Stable role labels for the phase contents. |

Each `navigation_pack.primary_target` or `navigation_pack.follow_up_reads[]` object uses:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute path for the target file. |
| `symbol` | `string` | Symbol or enclosing region to inspect next. |
| `start_line` | `integer` | Suggested start line for the next read. |
| `end_line` | `integer` | Suggested end line for the next read. |
| `lsp_provider_response` | `boolean` | Optional; `true` only when the target was confirmed by a completed LSP request. |
| `lsp_proof` | `boolean` | Optional; `true` only for marker-backed LSP evidence, never for provider availability alone. |
| `lsp_operation` | `string` | Optional LSP request that produced the evidence, such as `textDocument/definition`. |
| `lsp_resolution_basis` | `string` | Optional explanation for anchored LSP proof, such as `native-definition-anchor`. |
| `mention_ref` | `string` | Mention-ready source ref such as `path#L10-L18`. |
| `role` | `string` | Present on `follow_up_reads[]`; one of `primary`, `related`, or `test`. |
| `rationale` | `string` | Deterministic one-line explanation for why the range is worth reading next. |
| `reasons` | `array<string>` | Stable ranking reasons carried through from the planning path. |
| `provenance` | `array<string>` | Normalized trust labels when available. |

## Context Render JSON

Emitted by `tg.exe context-render <path> <query> --json`.

Example: [`examples/context_render.json`](examples/context_render.json)

Use this shape when an agent wants a prompt-ready bundle instead of only the raw ranked context inventory.

For CLI use, `tg.exe context-render --json` defaults to the `llm` render profile. That profile uses compact JSON wire formatting and intentionally omits duplicated top-level inventories such as `symbols`, `imports`, `related_paths`, `file_matches`, `file_summaries`, `test_matches`, `coverage`, and `candidate_edit_targets`; agents should read `rendered_context`, `sources`, `edit_plan_seed`, `navigation_pack`, and top-level `validation_commands` first. Use `--render-profile full` when a full pretty-printed inventory is required.

The `llm` profile is compact, not summary-only. Selected source blocks include executable body lines by default; compacting can strip comments, docstrings when optimization is requested, blank lines, type-only imports, and boilerplate, but it must not remove the behavior from a selected function.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `context-render` for direct builds, or `session-context-render` when top-level native-provider CLI requests reuse an already-running daemon. |
| `sidecar_used` | `boolean` | Always `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. Omitted by the CLI `llm` JSON profile. |
| `query` | `string` | Query text used for ranking and rendering. |
| `path` | `string` | Absolute root path inventoried. |
| `scan_limit` | `object` | Optional bounded-scan metadata with `max_repo_files`, `scanned_files`, and `possibly_truncated`. Agent-facing CLI commands default to bounded broad scans. |
| `files` | `array<string>` | Ranked source files included in the render bundle. |
| `file_matches` | `array<object>` | Ranked source file metadata with stable `path`, `score`, optional `graph_score`, and `reasons`. Omitted by the CLI `llm` JSON profile. |
| `file_summaries` | `array<object>` | Compact top-level symbol skeletons for the rendered files. Omitted by the CLI `llm` JSON profile. |
| `symbols` | `array<object>` | Ranked symbols that seeded the selected source blocks. Omitted by compact and `llm` profiles. |
| `imports` | `array<object>` | Ranked import rows carried through from the context pack path. Omitted by compact and `llm` profiles. |
| `tests` | `array<string>` | Ranked related tests. |
| `test_matches` | `array<object>` | Ranked related test metadata with `path`, `score`, optional `graph_score`, and `reasons`. Omitted by the CLI `llm` JSON profile. |
| `related_paths` | `array<string>` | Stable merged order of rendered source and test paths. Omitted by compact and `llm` profiles. |
| `sources` | `array<object>` | Exact source blocks selected from the highest-value ranked symbols, or bounded file snippets for ranked text/Markdown files that have no symbols. |
| `max_files` | `integer` | Maximum files allowed in the render bundle. |
| `max_sources` | `integer` | Maximum exact source blocks allowed in the render bundle. |
| `max_symbols_per_file` | `integer` | Maximum summary symbols emitted per file. |
| `max_render_chars` | `integer \| null` | Optional render-text budget applied to `rendered_context`. |
| `optimize_context` | `boolean` | Whether comment-only and blank lines were stripped from rendered source blocks. |
| `render_profile` | `string` | Render profile used for source compaction: `full`, `compact`, or `llm`. |
| `semantic_provider` | `string` | Effective semantic provider used for primary-target proof. Defaults to `native`; explicit `lsp` / `hybrid` modes may add row-level LSP proof fields only when a completed provider request contributed evidence. |
| `context_payload_profile` | `string` | Present for compact profiles, such as `llm-compact`. |
| `payload_compaction` | `object` | Present for compact profiles; records omitted keys and the applied source/file limits. |
| `truncated` | `boolean` | Whether `rendered_context` was clipped to satisfy `max_render_chars`. |
| `sections` | `array<object>` | Machine-readable section metadata for the rendered bundle, including byte offsets, section type, and provenance for why each section was included. |
| `candidate_edit_targets` | `object` | Highest-value files, symbols, tests, and ranked span anchors carried forward for downstream edit planning. Omitted by the CLI `llm` JSON profile. |
| `edit_plan_seed` | `object` | Default primary file/symbol/span, related spans, dependent files, edit ordering, structured validation plan, normalized confidence scores, and likely validation command seeds for downstream autonomous edit loops. |
| `navigation_pack` | `object` | Compact AI-facing navigation bundle mirroring Edit Plan JSON so planner/executor loops can reuse one shape. |
| `context_consistency` | `object` | Reports whether `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and rendered sections agree. Includes an omitted-primary reason and confidence-downgrade flag when budgets hide the primary target. |
| `validation_commands` | `array<string>` | Top-level copy of the best validation commands from `navigation_pack` or `edit_plan_seed` for quick agent access. |
| `rendered_context` | `string` | Deterministic text bundle ready for edit-planning prompts. |

`edit_plan_seed` currently includes:

- `primary_file`
- `primary_symbol`
- `primary_span`
- `primary_test`
- `validation_tests`
- `validation_plan`
- `validation_commands`
- `reasons`
- `confidence`
- `related_spans`
- `dependent_files`
- `edit_ordering`
- `rollback_risk`

Each `validation_plan[]` row includes `detection` with one of `detected`, `heuristic`, or `generic`. JavaScript package-manager commands are emitted only when package metadata exists. Python commands require tests, project markers, or Python layout evidence; when no runner evidence exists, the plan is empty instead of inventing a test command.

Each `sources[]` object may also include compact-render metadata when `optimize_context` is enabled:

- `render_profile`
- `optimize_context`
- `rendered_source`
- `line_map[]`
- `render_diagnostics`

For Python source blocks, compact and optimized `llm` profiles can strip:

- leading docstrings
- pure `pass` boilerplate in otherwise empty class/function bodies

## Agent Capsule JSON

Emitted by `tg.exe agent <path> <query> --json`.

Use this shape when an agent needs the smallest actionable work packet before editing code. It composes context rendering, edit-plan evidence, validation hints, and rollback guidance without changing raw `search --format rg`, `search --json`, or `search --ndjson` output contracts.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `agent-context-capsule`. |
| `capsule_version` | `integer` | Capsule schema version. Current value: `1`; future changes should be additive. |
| `capsule_kind` | `string` | `actionable_context`. |
| `query` | `string` | Natural-language task or symbol query used for ranking. |
| `path` | `string` | Absolute root path inventoried. |
| `semantic_provider` | `string` | Effective semantic provider used for the underlying context-render/edit-plan evidence. Defaults to `native`; explicit `lsp` / `hybrid` modes may mark `primary_target` with LSP proof fields when provider evidence exists. |
| `ambiguity` | `object` | Equal-confidence routing state with `status`, `requires_confirmation`, `tie_count`, and `tied_alternative_targets`; resolved ties also include `resolved_by`. |
| `primary_target` | `object` | Selected edit target with `file`, `symbol`, `kind`, `line`, `confidence`, and evidence labels. |
| `alternative_targets` | `array<object>` | Plausible non-primary targets surfaced when routing is ambiguous; entries include file/symbol/language evidence and do not raise primary confidence. |
| `route_rationale` | `array<object>` | Deterministic explanation of which route selected the target and why. |
| `snippets` | `array<object>` | Bounded executable source snippets with `file`, `symbol`, `start_line`, `end_line`, `source`, `line_map`, `token_estimate`, and evidence labels. |
| `related_call_sites` | `array<object>` | Verified call-site hints when available. Capsule v1 leaves this empty unless call-site evidence is explicitly collected; follow-up reads are reported under `omissions.follow_up_reads`. |
| `call_site_evidence` | `object` | Status metadata explaining whether call-site evidence was collected, disabled, or omitted. |
| `validation_plan` | `array<object>` | Structured validation hints copied from edit-plan evidence, including provenance fields such as `detection` when available. |
| `validation_commands` | `array<string>` | Top-level validation commands for quick agent access. Empty means no runner evidence was found. |
| `edit_order` | `array<string>` | Suggested file edit order. |
| `rollback` | `object` | Checkpoint recommendation plus shell-display `command` and machine-safe `argv` for rollback preparation. |
| `omissions` | `object` | Token budget, omitted section count, omitted section metadata, and follow-up read commands for recovering detail. |
| `confidence` | `object` | Overall score and downgrade reasons derived from ranking, omissions, and `context_consistency`. |
| `ask_user_before_editing` | `object` | `required` plus reasons when evidence is weak, validation is absent, snippets are missing, or context consistency was downgraded. |
| `context_consistency` | `object` | Same primary-target consistency contract as Context Render JSON. |
| `raw_context_ref` | `object` | Reproduction reference for the underlying context-render payload. Includes both shell-display `command` and machine-safe `argv`. |

Capsule-level `context_consistency` extends the Context Render JSON contract with `capsule_primary_file_in_snippets`, `capsule_primary_file_in_follow_up_reads`, and `capsule_primary_file_omitted`. When a token budget omits the primary file from `snippets`, the capsule must report the omission reason, include a follow-up read when available, downgrade `confidence`, and set `ask_user_before_editing.required = true`.

Equal-confidence target ties are exposed twice: `context_consistency` keeps machine-checkable tie details, while top-level `ambiguity` gives agents a compact edit-safety decision. Unresolved ties use `ambiguity.status = "tie_requires_confirmation"` and force `requires_confirmation = true`; this is a hard stop for autonomous edits. Ties resolved by explicit evidence use `status = "tie_resolved"` with `resolved_by` naming the evidence source, such as aligned validation imports or explicit language intent.

Recovery references such as `raw_context_ref` and `omissions.follow_up_reads[]` include:

| Field | Type | Notes |
| --- | --- | --- |
| `command` | `string` | Shell-display command with arguments quoted for paths or queries that contain spaces. |
| `argv` | `array<string>` | Machine-safe argument vector preferred by subprocess callers. |

The text form of `tg agent` prints only a short summary with the query, primary target, validation command count, and confidence. Use `--json` for the stable capsule contract.

## Rewrite Plan JSON

Emitted by `tg.exe run --rewrite <replacement> <pattern> <path>` when `--diff` and `--apply` are not set.

Optional edit selection flags:

- `--apply-edit-ids <id1,id2,...>` keeps only the listed planned edit IDs
- `--reject-edit-ids <id1,id2,...>` drops the listed planned edit IDs

These flags filter `edits[]` before diff/apply/verify execution and fail closed on unknown or duplicate IDs.

`tg.exe run --batch-rewrite <config.json> <path>` emits the same common rewrite-plan envelope, but replaces the single `pattern` / `replacement` / `lang` fields with a `rewrites` array copied from the config file.

Example: [`examples/rewrite_plan.json`](examples/rewrite_plan.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `pattern` | `string` | Structural search pattern. |
| `replacement` | `string` | Rewrite template. |
| `lang` | `string` | Tree-sitter language passed with `--lang`. |
| `total_files_scanned` | `integer` | Files walked during planning. |
| `total_edits` | `integer` | Accepted edits in `edits`. |
| `edits` | `array<object>` | Concrete edit plan. |
| `rejected_overlaps` | `array<object>` | Optional; only present when overlapping edits are rejected. |

Each `edits[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `string` | Stable edit identifier. |
| `file` | `string` | File being rewritten. |
| `line` | `integer` | 1-based source line for the match. |
| `byte_range.start` | `integer` | Inclusive byte start in the original file. |
| `byte_range.end` | `integer` | Exclusive byte end in the original file. |
| `original_text` | `string` | Original matched text. |
| `replacement_text` | `string` | Final replacement text to write. |
| `metavar_env` | `object<string,string>` | Bound metavariables captured from the match. |

If `rejected_overlaps` is present, each object contains `file`, `edit_a`, `edit_b`, and `reason`.

## Batch Rewrite Config

Batch rewrite is configured with `tg.exe run --batch-rewrite <config.json> <path>`.

Config schema:

```json
{
  "rewrites": [
    {
      "pattern": "def $F($$$ARGS): return $EXPR",
      "replacement": "lambda $$$ARGS: $EXPR",
      "lang": "python"
    }
  ],
  "verify": true
}
```

Rules:

- `rewrites` is required and must be a non-empty array.
- Each rewrite object must include string `pattern`, `replacement`, and `lang` fields.
- `verify` is optional; if present it must be a boolean and enables post-apply byte-level verification for batch apply.
- Invalid configs fail with field-specific errors such as `rewrites[0].replacement`.

Batch planning/apply behavior:

- all configured patterns are planned against the original file contents before any write occurs
- `rejected_overlaps` reports cross-pattern conflicts, and conflicted files are left unchanged
- batch apply reuses the same atomic-write, BOM/CRLF preservation, binary-skip, and stale-file protections as single rewrites

## Apply + Verify JSON

Emitted by `tg.exe run --rewrite ... --apply --verify --json ...`.

Optional edit selection flags:

- `--apply-edit-ids <id1,id2,...>`
- `--reject-edit-ids <id1,id2,...>`
- `--lint-cmd <command>`
- `--test-cmd <command>`
- `--audit-manifest <path>`

When edit selection flags are present, the emitted `plan` object reflects the filtered subset that was actually applied and verified.
When validation flags are present, the emitted payload also includes a structured `validation` object describing each post-apply command.
Validation command templates may use `$file` or `{file}` for edited file paths. Commands with a file placeholder run once per edited file after apply; commands without a file placeholder run once in the original target working directory. Quote the placeholder in the command template when paths may contain spaces, for example `--lint-cmd 'python -m py_compile "$file"'` in PowerShell.

Example: [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `checkpoint` | `object \| null` | Present when `--checkpoint` is requested before apply; otherwise `null` or omitted. |
| `audit_manifest` | `object \| null` | Present when `--audit-manifest <path>` is requested; otherwise `null` or omitted. |
| `plan` | `object` | Full rewrite plan object, using the same shape as Rewrite Plan JSON. |
| `validation` | `object \| null` | Present when `--lint-cmd` and/or `--test-cmd` is requested; otherwise `null` or omitted. |
| `verification` | `object \| null` | Present when `--verify` is requested; otherwise `null`. |

`verification` currently contains:

| Field | Type | Notes |
| --- | --- | --- |
| `total_edits` | `integer` | Total planned edits checked after apply. |
| `verified` | `integer` | Edits whose replacement bytes matched exactly. |
| `mismatches` | `array<object>` | Empty on success. |

Each `mismatches[]` object contains `edit_id`, `file`, `line`, `expected`, and `actual`.

`validation` currently contains:

| Field | Type | Notes |
| --- | --- | --- |
| `success` | `boolean` | `true` only when all requested post-apply commands succeeded. |
| `commands` | `array<object>` | Ordered list of executed validation commands. |

Each `commands[]` object contains `kind`, `command`, `success`, `exit_code`, `stdout`, and `stderr`.

When `--audit-manifest <path>` is present, the payload also includes:

| Field | Type | Notes |
| --- | --- | --- |
| `audit_manifest.path` | `string` | Absolute or caller-provided output path written for the manifest. |
| `audit_manifest.file_count` | `integer` | Number of files included in the manifest. |
| `audit_manifest.applied_edit_count` | `integer` | Number of applied edit IDs recorded in the manifest. |
| `audit_manifest.signed` | `boolean` | Whether the manifest was signed. |
| `audit_manifest.signature_kind` | `string \| null` | Signature algorithm summary, currently `hmac-sha256` when signed. |

The on-disk audit manifest itself is a deterministic JSON document that includes:

- `manifest_sha256`: self-digest over the canonical manifest JSON without the digest field
- `previous_manifest_sha256`: digest of the previous manifest written to the same path, when present
- `signature`: optional keyed signature block when `--audit-signing-key <path>` is used

## Attempt Ledger JSON

Emitted by `python benchmarks/build_attempt_ledger.py --input <path> --output <path>`.

Examples: [`examples/attempt_ledger.json`](examples/attempt_ledger.json), [`examples/multi_session_attempt_ledger.json`](examples/multi_session_attempt_ledger.json), [`examples/multi_task_attempt_ledger.json`](examples/multi_task_attempt_ledger.json)

Emitted by `python benchmarks/build_attempt_ledger.py --input <spec.json> --output <artifact.json>`.

Use this shape when an external agent needs machine-readable provenance across more than one edit attempt for the same task. The goal is to make retries, replay, and final acceptance auditable without scraping prose from logs. In practice this is the stable attempt ledger / replay chain contract for partial retry, audit-safe resumption, multi-session replay handoff, and multi-task replay across a bounded task chain. The contract can be materialized either by `python benchmarks/build_attempt_ledger.py --input <path> --output <path>`, directly from the patch-driver flow via `python benchmarks/run_tensor_grep_patch_driver.py --attempt-ledger-output <path>`, from scored patch-eval output via `python benchmarks/run_patch_bakeoff.py --scenarios <path> --predictions <path> --attempt-ledger-dir <dir>`, from the Claude A/B producer via `python benchmarks/run_claude_skill_ab.py --input <path> --attempt-ledger-dir <dir>`, from the Gemini A/B producer via `python benchmarks/run_gemini_skill_ab.py --input <path> --attempt-ledger-dir <dir>`, or from the competitor prediction producers via `python benchmarks/run_claude_patch_predictions.py --input <path> --attempt-ledger-dir <dir>`, `python benchmarks/run_copilot_patch_predictions.py --input <path> --attempt-ledger-dir <dir>`, and `python benchmarks/run_gemini_patch_predictions.py --input <path> --attempt-ledger-dir <dir>`.

When you need a single comparison surface across multiple external validation runs, `python benchmarks/build_external_agent_patch_driver_comparison.py --summary <system>=<summary.json> ... --output <path>` normalizes those per-system patch-driver summaries into one `external_agent_patch_driver_comparison` artifact while preserving each system's chosen primary file, follow-up count, validation commands, and phased `parallel_read_groups` when the underlying patch-driver output carries the current `navigation_pack` contract.

To quantify whether that handoff stays small, stack-aware, and materially more parallel than a flat follow-up list, `python benchmarks/build_external_agent_patch_driver_scorecard.py --input <comparison.json> --output <scorecard.json>` scores each system on compactness (`follow_up_count <= 5`), validation-fit (whether the suggested validation command matches the local stack inferred from the primary file), and phased-read reduction (how many serial follow-up read steps are eliminated by `parallel_read_groups`).

| Field | Type | Notes |
| --- | --- | --- |
| `artifact` | `string` | Stable artifact label. The committed example uses `agent_attempt_ledger`. |
| `suite` | `string` | Ledger producer name. The committed example uses `agent_loop`. |
| `generated_at_epoch_s` | `number` | Unix timestamp written when the ledger was materialized. |
| `task_id` | `string` | Stable task or issue identifier shared by all attempts. |
| `root` | `string` | Repository root associated with the attempt chain. |
| `attempts` | `array<object>` | Ordered attempt ledger. One row per materialized attempt. |
| `final_outcome` | `object` | Accepted or terminal rejected outcome for the chain. |
| `replay` | `object` | Replay/audit instructions for consumers that need to resume or re-audit the chain. |

Each `attempts[]` object includes:

| Field | Type | Notes |
| --- | --- | --- |
| `attempt_id` | `string` | Stable attempt identifier. |
| `parent_attempt_id` | `string \| null` | Previous attempt in the retry chain when present. |
| `kind` | `string` | Current example uses `rewrite_apply_verify`. |
| `status` | `string` | Attempt lifecycle such as `validation_failed` or `accepted`. |
| `retryable` | `boolean` | Whether the next controller step may retry this attempt. |
| `retry_stage` | `string` | Narrowest safe replay boundary such as `validation`, `plan`, or `full_attempt`. |
| `retry_reason` | `string` | Stable replay rationale. |
| `checkpoint_id` | `string \| null` | Checkpoint available for rollback or replay. |
| `audit_manifest_path` | `string \| null` | Audit manifest associated with the attempt when present. |
| `validation_success` | `boolean` | Whether repo validation passed for this attempt. |
| `score_artifact` | `string \| null` | Final-score artifact emitted for the attempt when present. |
| `inputs` | `array<string>` | Machine-readable input artifacts consumed by this attempt. |
| `outputs` | `array<string>` | Machine-readable output artifacts emitted by this attempt. |

`final_outcome` includes:

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `string` | Terminal outcome such as `accepted` or `rejected`. |
| `accepted_attempt_id` | `string \| null` | Accepted attempt when one exists. |
| `score_artifact` | `string \| null` | Final machine-readable score artifact associated with the accepted or terminal attempt. |
| `summary` | `string` | Short human-readable outcome summary for audit reports. |

`replay` includes:

| Field | Type | Notes |
| --- | --- | --- |
| `preserve_attempt_ids` | `boolean` | Consumers should preserve existing IDs rather than rewriting history. |
| `partial_retry_ledger` | `array<object>` | Ordered replay decisions describing which stage was retried and why. |
| `audit_chain` | `array<string>` | Ordered manifest or trust artifacts for replay/audit validation. |
| `next_action` | `string` | Recommended next controller step when the chain is resumed. |

Multi-session replay adds:

| Field | Type | Notes |
| --- | --- | --- |
| `attempts[].session_id` | `string \| null` | Session that produced the attempt when session reuse is active. |
| `replay.multi_session` | `boolean` | `true` when the ledger crosses more than one cached session. |
| `replay.handoff` | `object \| null` | Ordered handoff metadata with `from_session_id`, `to_session_id`, and `reason`. |

Multi-task replay adds:

| Field | Type | Notes |
| --- | --- | --- |
| `tasks` | `array<object>` | Ordered task inventory when the ledger spans more than one task. |
| `tasks[].task_id` | `string` | Stable task identifier for each task in the chain. |
| `tasks[].status` | `string` | Terminal or in-progress task state such as `accepted` or `rolled_forward`. |
| `tasks[].accepted_attempt_id` | `string \| null` | Accepted attempt associated with that task when present. |
| `replay.multi_task` | `boolean` | `true` when the ledger preserves a multi-task replay chain instead of a single-task retry chain. |
| `replay.task_chain` | `array<string>` | Ordered task IDs that define the replay chain across tasks. |

## Patch Bakeoff JSON

Emitted by `python benchmarks/run_patch_bakeoff.py --scenarios <path> --predictions <path> --output <path>`.

Example: [`examples/patch_bakeoff.json`](examples/patch_bakeoff.json)

Use this shape when an agent needs a machine-readable final score for one or more patch attempts against a fixed scenario pack.

| Field | Type | Notes |
| --- | --- | --- |
| `artifact` | `string` | Always `bench_patch_bakeoff`. |
| `suite` | `string` | Always `run_patch_bakeoff`. |
| `generated_at_epoch_s` | `number` | Unix timestamp for the scoring run. |
| `environment` | `object` | Host metadata for auditability. |
| `summary` | `object` | Aggregate score surface across all scored rows. |
| `rows` | `array<object>` | One scored row per `(instance_id, system)` prediction pair. |

`summary` includes:

| Field | Type | Notes |
| --- | --- | --- |
| `scenario_count` | `integer` | Number of scored rows, not the source scenario count. |
| `missing_predictions` | `array<string>` | Scenario IDs with no prediction rows; retry the producer, not the scorer. |
| `mean_patch_applied_rate` | `number` | Fraction of rows whose patch applied cleanly. |
| `mean_validation_pass_rate` | `number` | Fraction of rows whose validation commands passed after apply. |
| `mean_primary_file_hit_rate` | `number` | Fraction of rows that touched the expected primary file. |
| `mean_primary_span_hit_rate` | `number` | Fraction of rows that touched the expected primary span. |
| `mean_changed_file_recall` | `number` | Average changed-file recall versus expected files. |
| `mean_changed_file_precision` | `number` | Average changed-file precision versus expected files. |
| `mean_predicted_test_hit_rate` | `number` | Average predicted-test hit rate. |
| `mean_predicted_validation_cmd_hit_rate` | `number` | Average predicted validation-command hit rate. |

Each `rows[]` object includes:

| Field | Type | Notes |
| --- | --- | --- |
| `instance_id` | `string` | Scenario identifier. |
| `system` | `string` | Comparator label such as `claude-enhanced`, `copilot`, or `gemini-baseline`. |
| `patch_applied` | `boolean` | Whether `git apply` succeeded. |
| `validation_passed` | `boolean` | Whether all validation commands passed after apply. |
| `reason` | `string` | Stable row-level result classification such as `ok`, `no patch emitted`, `timeout after 60s`, `patch apply failed`, or `validation failed`. |
| `apply_error` | `string` | Apply failure text, or empty string when apply succeeded or no patch was emitted. |
| `actual_changed_files` | `array<string>` | Files changed by the predicted patch. |
| `primary_file_hit` | `number` | `1.0` when the expected primary file was touched, otherwise `0.0`. |
| `primary_span_hit` | `number` | `1.0` when the expected primary span was touched, otherwise `0.0`. |
| `changed_file_recall` | `number` | Recall against expected changed files. |
| `changed_file_precision` | `number` | Precision against expected changed files. |
| `unexpected_files_touched` | `array<string>` | Files touched outside the expected set. |
| `predicted_test_hit_rate` | `number` | Predicted-test hit rate against expected test files. |
| `predicted_validation_cmd_hit_rate` | `number` | Predicted validation-command hit rate. |
| `validation_results` | `array<object>` | Per-command post-apply validation results when apply succeeded. |

## Audit Manifest Verify JSON

Emitted by `tg.exe audit-verify <manifest> --json`.

Example: [`examples/audit_manifest_verify.json`](examples/audit_manifest_verify.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AuditManifest`. |
| `routing_reason` | `string` | `audit-manifest-verify`. |
| `sidecar_used` | `boolean` | Always `false`. |
| `manifest_path` | `string` | Resolved path to the manifest being verified. |
| `signing_key_path` | `string \| null` | Signing key path used for signature verification when present. |
| `previous_manifest_path` | `string \| null` | Previous manifest used for chain validation when present. |
| `kind` | `string \| null` | Manifest kind from the on-disk payload. |
| `manifest_sha256` | `string \| null` | Recorded manifest self-digest from the payload. |
| `previous_manifest_sha256` | `string \| null` | Recorded previous-manifest digest from the payload. |
| `checks` | `object` | Structured verification results. |
| `signature_kind` | `string \| null` | Signature algorithm summary, currently `hmac-sha256` when signed. |
| `valid` | `boolean` | `true` only when digest, chain, and signature checks all pass. |
| `errors` | `array<string>` | Ordered list of verification failures. |

`checks` currently contains `digest_valid`, `chain_valid`, and `signature_valid`.

## GPU CPU Fallback JSON

Emitted by `tg.exe search --gpu-device-ids <ids> --json ...` when the request cannot use `NativeGpuBackend` and the runtime falls back to native CPU search instead of the Python sidecar.

Example: [`examples/gpu_cpu_fallback_search.json`](examples/gpu_cpu_fallback_search.json)

This is not GPU acceleration proof. It preserves the requested devices for observability while making the actual route unambiguous:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Always `1` for the current contract. |
| `routing_backend` | `string` | `NativeCpuBackend` for CPU fallback. |
| `routing_reason` | `string` | `gpu-auto-fallback-cpu`. |
| `sidecar_used` | `boolean` | `false`; no Python sidecar handled the search. |
| `requested_gpu_device_ids` | `array<integer>` | GPU IDs requested by the caller. |
| `routing_gpu_device_ids` | `array<integer>` | Empty because no GPU handled the route. |
| `query` | `string` | Search pattern exactly as passed on the command line. |
| `path` | `string` | Search root passed to the command. |
| `total_matches` | `integer` | Number of materialized matches in `matches`. |
| `matches` | `array<object>` | Native CPU match rows using `file`, `line`, and `text`. |

## GPU Sidecar JSON

Emitted by `tg.exe search --gpu-device-ids <ids> --json ...`.

Example: [`examples/gpu_sidecar_search.json`](examples/gpu_sidecar_search.json)

This is a hybrid contract:

- the Rust control plane injects the unified envelope (`version`, `routing_backend`, `routing_reason`, `sidecar_used`)
- the nested search payload comes from Python sidecar JSON

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Added by Rust. |
| `routing_backend` | `string` | `GpuSidecar`, added by Rust. |
| `routing_reason` | `string` | `gpu-device-ids-explicit`, added by Rust. |
| `sidecar_used` | `boolean` | `true`, added by Rust. |
| `total_matches` | `integer` | Preserved from sidecar payload. |
| `total_files` | `integer` | Preserved from sidecar payload. |
| `requested_gpu_device_ids` | `array<integer>` | GPU IDs requested by the caller, added by the Rust front door. |
| `routing_gpu_device_ids` | `array<integer>` | Device IDs reported by the sidecar payload. |
| `matches` | `array<object>` | Sidecar match rows. |

Each GPU sidecar `matches[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `file` | `string` | Absolute file path. |
| `line_number` | `integer` | 1-based line number from the Python sidecar. |
| `text` | `string` | Matching line text. |

On this worker host the real GPU Python backends were unavailable, so the committed example was produced by running the real native `tg.exe` command against `bench_data/` with `TG_SIDECAR_SCRIPT` set to a deterministic mock. That still exercises the Rust sidecar transport and envelope normalization path.

## Calibrate JSON

Emitted by `tg.exe calibrate`.

Example: [`examples/calibrate.json`](examples/calibrate.json)

This shape is the persisted routing calibration contract consumed by the native Rust router.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. Current value: `1`. |
| `routing_backend` | `string` | `Calibration` for the committed example. |
| `routing_reason` | `string` | `manual-calibrate` for the committed example. |
| `sidecar_used` | `boolean` | Always `false` for the native calibrate command. |
| `corpus_size_breakpoint_bytes` | `integer` | Smallest corpus size where GPU became the recommended route in the calibrated run. |
| `cpu_median_ms` | `number` | Representative CPU median at the chosen breakpoint. |
| `gpu_median_ms` | `number` | Representative GPU median at the chosen breakpoint. |
| `recommendation` | `string` | Stable routing recommendation such as `gpu_above_100mb` or `cpu_always`. |
| `calibration_timestamp` | `integer` | Unix timestamp written with the accepted calibration result. |
| `device_name` | `string` | Device name associated with the calibration run. |
| `measurements` | `array<object>` | Calibration points used to derive the recommendation. |

Each `measurements[]` object has:

| Field | Type | Notes |
| --- | --- | --- |
| `size_bytes` | `integer` | Corpus size benchmarked at this point. |
| `cpu_median_ms` | `number` | CPU median for the point. |
| `gpu_median_ms` | `number` | GPU median for the point. |
| `cpu_samples_ms` | `array<number>` | Raw CPU timing samples retained for auditability. |
| `gpu_samples_ms` | `array<number>` | Raw GPU timing samples retained for auditability. |

## Search NDJSON

Emitted by `tg.exe search --ndjson ...`.

Example: [`examples/search.ndjson`](examples/search.ndjson)

This is the streaming variant of Search JSON. Each line is a standalone JSON object with the common envelope plus a single match row.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | Backend selected by the Rust router. |
| `routing_reason` | `string` | Stable reason for the route. |
| `sidecar_used` | `boolean` | `false` for the committed native example. |
| `requested_gpu_device_ids` | `array<integer>` | Explicit GPU IDs requested by the caller; empty for CPU search. |
| `routing_gpu_device_ids` | `array<integer>` | GPU IDs actually used by the runtime route. Empty for native CPU, index, and CPU fallback routes. |
| `query` | `string` | Search pattern. |
| `path` | `string` | Search root. |
| `file` | `string` | Absolute path of the matched file for this row. |
| `line` | `integer` | 1-based line number for this row. |
| `text` | `string` | Matching line text. |
| `pattern_id` | `integer \| null` | Present for multi-pattern routes. |
| `pattern_text` | `string \| null` | Present when `pattern_id` is present. |

## Symbol Defs JSON

Emitted by `tg.exe defs <path> <name> --json`.

Example: [`examples/defs.json`](examples/defs.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-defs`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name requested. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Native-vs-provider merge summary including agreement status, counts, and fallback usage. |
| `provider_status` | `object` | Provider health snapshot including attempted providers, capabilities, `health_status`, `health_check`, and last error. `available=true` means the provider command was found; it is not proof that initialization or navigation requests succeeded. |
| `lsp_proof` | `boolean` | `true` only when an external LSP provider contributed usable evidence for the response. Navigation rows must carry `lsp_provider_response = true`; provenance strings alone do not count as proof. |
| `lsp_evidence_status` | `string` | LSP evidence status such as `not_requested`, `lsp_proof`, `fallback_native`, or `no_lsp_evidence`. |
| `not_lsp_proof_reason` | `string` | Optional reason explaining why the response should not be treated as LSP-confirmed. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `graph_completeness` | `string` | Trust label for the returned definition graph, currently `strong`. |
| `no_match` | `boolean` | Optional; `true` when no exact definition was found. No-match payloads intentionally keep `files`, `symbols`, `imports`, `tests`, and `related_paths` empty. |
| `message` | `string` | Optional human-readable no-match summary. |
| `files` | `array<string>` | Files containing exact definitions. |
| `tests` | `array<string>` | Test files in the inventory root. |
| `related_paths` | `array<string>` | Stable union of definition files and tests. |

Each `definitions[]` object contains `name`, `kind`, `file`, `line`, and may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Symbol-navigation source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |
| `lsp_provider_response` | `boolean` | Present and `true` only for rows returned by a completed external LSP request. Required before a row can contribute to top-level `lsp_proof`. |
| `lsp_operation` | `string` | Provider operation that produced the row, for example `workspace/symbol` or `textDocument/references`. |

## Symbol Source JSON

Emitted by `tg.exe source <path> <name> --json`.

Example: [`examples/source.json`](examples/source.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-source`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name requested. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Same native-vs-provider merge summary exposed by Symbol Defs JSON. |
| `provider_status` | `object` | Same provider health snapshot exposed by Symbol Defs JSON. |
| `lsp_proof` | `boolean` | Same provider-evidence proof flag exposed by Symbol Defs JSON. |
| `lsp_evidence_status` | `string` | Same provider-evidence status exposed by Symbol Defs JSON. |
| `not_lsp_proof_reason` | `string` | Optional reason explaining why the response should not be treated as LSP-confirmed. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `sources` | `array<object>` | Exact Python blocks or heuristic JS/TS/Rust blocks for the resolved symbol. |
| `no_match` | `boolean` | Optional; `true` when no exact definition/source block was found. No-match payloads intentionally stay compact. |
| `message` | `string` | Optional human-readable no-match summary. |
| `files` | `array<string>` | Files containing exact definitions. |
| `tests` | `array<string>` | Test files in the inventory root. |
| `related_paths` | `array<string>` | Stable union of definition files and tests. |

Each `sources[]` object contains `name`, `kind`, `file`, `start_line`, `end_line`, and `source`.

## Symbol Impact JSON

Emitted by `tg.exe impact <path> <name> --json`.

Example: [`examples/impact.json`](examples/impact.json)

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-impact`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Same native-vs-provider merge summary exposed by Symbol Defs JSON. |
| `provider_status` | `object` | Same provider health snapshot exposed by Symbol Defs JSON. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `files` | `array<string>` | Likely impacted source files, definition file first. |
| `file_matches` | `array<object>` | Ranked impacted file metadata with stable `path`, `score`, and provenance `reasons`. |
| `file_summaries` | `array<object>` | Compact top-level symbol skeletons for the impacted files. |
| `tests` | `array<string>` | Likely impacted tests. |
| `test_matches` | `array<object>` | Ranked impacted test metadata with stable `path`, `score`, and provenance `reasons`. |
| `imports` | `array<object>` | Ranked import entries from the context pack path. |
| `symbols` | `array<object>` | Ranked related symbols, including `score`. |
| `related_paths` | `array<string>` | Stable union of impacted files and tests. |

Each `imports[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Import-source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

## Symbol Refs JSON

Emitted by `tg.exe refs <path> <name> --json`.

Example: [`examples/refs.json`](examples/refs.json)

This is currently a Python-first symbol navigation contract. It finds exact name/attribute references from Python ASTs and does not claim full cross-language semantic resolution.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-refs`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Native-vs-provider merge summary for reference discovery. |
| `provider_status` | `object` | Provider health snapshot for reference discovery. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `graph_completeness` | `string` | Trust label for the returned definition graph, currently `strong`. |
| `references` | `array<object>` | Python-first reference rows. |
| `files` | `array<string>` | Files containing reference rows. |
| `related_paths` | `array<string>` | Stable union of definition files, reference files, and tests. |

Each `definitions[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Symbol-navigation source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

Each `references[]` object contains `name`, `kind`, `file`, `line`, `text`, and may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Symbol-navigation source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

## Symbol Callers JSON

Emitted by `tg.exe callers <path> <name> --json`.

Example: [`examples/callers.json`](examples/callers.json)

This is currently a Python-first symbol navigation contract. It finds exact Python call sites by name/attribute match and combines them with likely impacted tests.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-callers`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Native-vs-provider merge summary for caller discovery. |
| `provider_status` | `object` | Provider health snapshot for caller discovery. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `callers` | `array<object>` | Python-first call rows. |
| `files` | `array<string>` | Files containing call sites. |
| `tests` | `array<string>` | Likely impacted tests. |
| `related_paths` | `array<string>` | Stable union of definition files, caller files, and tests. |

Each `callers[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Symbol-navigation source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

## Symbol Blast Radius JSON

Emitted by `tg.exe blast-radius <path> <name> --json`.

Example: [`examples/blast_radius.json`](examples/blast_radius.json)

Use this shape when an agent needs an explicit downstream change radius instead of only flat caller rows or ranked impact files.

Use `--max-callers <n>` and `--max-files <n>` for bounded agent loops. When either limit is present, the payload includes `output_limit` metadata and caps `callers`, `caller_tree`, `files`, `file_matches`, `file_summaries`, `tests`, `test_matches`, `related_paths`, `symbols`, and `imports`; each retained `file_summaries[].symbols` list is also capped to compact symbol records. Capped no-match scans may include `scan_limit.literal_seed_files` when a literal symbol seed outside the initial repo-map cap recovered the definition.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `symbol-blast-radius`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Inventory root. |
| `scan_limit` | `object` | Optional bounded-scan metadata with `max_repo_files`, `scanned_files`, `possibly_truncated`, and optional `literal_seed_files` when capped symbol seeding was used. |
| `symbol` | `string` | Exact symbol name evaluated. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Same native-vs-provider merge summary exposed by Symbol Callers JSON. |
| `provider_status` | `object` | Same provider health snapshot exposed by Symbol Callers JSON. |
| `max_depth` | `integer` | Maximum reverse-import depth included in the radius. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `callers` | `array<object>` | Exact caller rows discovered by the symbol navigation layer. |
| `files` | `array<string>` | Files inside the computed blast radius, ordered by depth then score. |
| `file_matches` | `array<object>` | Ranked file metadata with `path`, `depth`, `score`, optional `graph_score`, and `reasons`. |
| `file_summaries` | `array<object>` | Top-level symbol skeletons for ranked radius files. |
| `tests` | `array<string>` | Likely validation tests covering the radius. |
| `test_matches` | `array<object>` | Ranked test metadata for the same radius. |
| `caller_tree` | `array<object>` | Depth-indexed radius tree with one object per depth level. |
| `rendered_caller_tree` | `string` | Deterministic text rendering of `caller_tree`. |
| `graph_trust_summary` | `object` | Top-level dependency-edge trust summary aggregated from the caller tree, including edge kind, confidence, provenance, depth count, and evidence counts. |
| `imports` | `array<object>` | Ranked imports reused from the impact surface. |
| `symbols` | `array<object>` | Ranked symbol matches reused from the impact surface. |
| `related_paths` | `array<string>` | Stable union of radius files and tests. |
| `graph_completeness` | `string` | Optional graph trust label surfaced on caller-tree nodes and related graph metadata. |
| `output_limit` | `object` | Present when caller/file output budgets are active. Raw CLI `blast-radius` defaults to a 25-caller / 25-file budget; pass `--max-callers` or `--max-files` to raise or lower it. Includes `max_callers`, `max_files`, `callers_truncated`, `files_truncated`, total/returned/omitted caller counts, and total/returned/omitted file counts. |

Each `definitions[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `string` | Symbol-navigation source label such as `python-ast`, `tree-sitter`, or `regex-heuristic`. |

Each `caller_tree[]` object may additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `provenance` | `array<string>` | Graph-source labels for the depth bucket, currently `graph-derived`. |
| `graph_completeness` | `string` | Actionable graph trust label, currently `moderate`. |
| `edge_summary` | `object` | Dependency-edge trust metadata for the depth bucket, including `edge_kind`, `confidence`, normalized provenance, and parser-backed vs heuristic evidence counts. |

## Symbol Blast Radius Plan JSON

Emitted by `tg.exe blast-radius-plan <path> <name> --json`.

Example: [`examples/blast_radius_plan.json`](examples/blast_radius_plan.json)

Use this shape when an agent needs the transitive blast radius plus ranked edit targets, but does not need the rendered prompt bundle.

The shape matches Symbol Blast Radius JSON and additionally includes:

| Field | Type | Notes |
| --- | --- | --- |
| `routing_reason` | `string` | `symbol-blast-radius-plan`. |
| `query` | `string` | Deterministic planning query used to seed the edit plan, currently `blast radius: <symbol>`. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Same native-vs-provider merge summary exposed by Symbol Blast Radius JSON. |
| `provider_status` | `object` | Same provider health snapshot exposed by Symbol Blast Radius JSON. |
| `max_files` | `integer` | Maximum files retained in the plan payload. |
| `max_symbols` | `integer` | Maximum ranked symbols retained in the plan payload. |
| `candidate_edit_targets` | `object` | Highest-value files, symbols, tests, and ranked span anchors carried forward for downstream edit planning. |
| `edit_plan_seed` | `object` | Primary file/symbol/span, related spans, dependent files, edit ordering, structured validation plan, validation commands, and rollback risk. |
| `navigation_pack` | `object` | Compact AI-facing navigation bundle carrying mention-ready follow-up reads and validation targets for the blast-radius plan. |
| `graph_trust_summary` | `object` | Same aggregated dependency-edge trust summary exposed by Symbol Blast Radius JSON. |

## Symbol Blast Radius Render JSON

Emitted by `tg.exe blast-radius-render <path> <name> --json`.

Example: [`examples/blast_radius_render.json`](examples/blast_radius_render.json)

Use this shape when an agent needs a prompt-ready transitive impact bundle seeded from a specific symbol instead of a free-text query.

The shape matches Context Render JSON and additionally includes:

| Field | Type | Notes |
| --- | --- | --- |
| `routing_reason` | `string` | `symbol-blast-radius-render`. |
| `symbol` | `string` | Exact symbol used to seed the blast radius. |
| `semantic_provider` | `string` | Effective semantic provider used for navigation, currently `native`, `lsp`, or `hybrid`. |
| `provider_agreement` | `object` | Same native-vs-provider merge summary exposed by Symbol Blast Radius JSON. |
| `provider_status` | `object` | Same provider health snapshot exposed by Symbol Blast Radius JSON. |
| `max_depth` | `integer` | Maximum reverse-import depth included in the rendered radius. |
| `definitions` | `array<object>` | Exact symbol definitions. |
| `callers` | `array<object>` | Exact caller rows. |
| `caller_tree` | `array<object>` | Depth-indexed radius tree. |
| `rendered_caller_tree` | `string` | Deterministic text rendering of the same tree. |
| `navigation_pack` | `object` | Compact AI-facing navigation bundle mirroring Edit Plan JSON so review or patch loops can consume the blast radius with minimal follow-up reads. |
| `graph_trust_summary` | `object` | Same aggregated dependency-edge trust summary exposed by Symbol Blast Radius JSON. |

## Session Open JSON

Emitted by `tg.exe session open ... --json`.

Example: [`examples/session_open.json`](examples/session_open.json)

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `string` | Stable identifier for later session queries. |
| `root` | `string` | Session root. |
| `created_at` | `string` | ISO-8601 timestamp for the cached repo map. |
| `file_count` | `integer` | Number of source files captured in the cached repo map. |
| `symbol_count` | `integer` | Number of symbols captured in the cached repo map. |

## Session Refresh JSON

Emitted by `tg session refresh <id> ... --json`.

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `string` | Session identifier refreshed in place. |
| `root` | `string` | Session root. |
| `refreshed_at` | `string` | ISO-8601 refresh timestamp. |
| `file_count` | `integer` | Number of source files captured after refresh. |
| `symbol_count` | `integer` | Number of symbols captured after refresh. |

## Session Daemon JSON

Emitted by:

- `tg.exe session daemon start ... --json`
- `tg.exe session daemon status ... --json`
- `tg.exe session daemon stop ... --json`

This is the root-scoped warm localhost daemon that backs daemon-routed session requests.

Start and status responses include:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `root` | `string` | Session root served by the daemon. |
| `running` | `boolean` | Whether the daemon is currently live. |
| `host` | `string` | Loopback host used for request routing. |
| `port` | `integer` | Bound localhost port. |
| `pid` | `integer` | Process identifier when the daemon is live. |
| `started_at` | `string` | ISO-8601 startup timestamp when the daemon is live. |
| `response_cache_scope` | `string` | Public scope for `response_cache_*` counters. Current value: `daemon-routed top-level/session context-render/edit-plan requests`; top-level native-provider `tg context-render` and `tg edit-plan` are counted when they reuse an already-running daemon. Implicit sessions are keyed by root and `max_repo_files` so larger-budget calls do not reuse smaller cached maps. |

Stop responses additionally include:

| Field | Type | Notes |
| --- | --- | --- |
| `stopped` | `boolean` | `true` when a live daemon accepted the shutdown request. |

## Session Context JSON

Emitted by `tg.exe session context <id> <path> <query> --json`.

Example: [`examples/session_context.json`](examples/session_context.json)

This reuses a cached repo map instead of rebuilding inventory for every query.
Use `--daemon` to route the same request through the warm localhost session daemon. Daemon-routed
responses preserve the same payload shape and add `serve_cache` request provenance.

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `RepoMap`. |
| `routing_reason` | `string` | `session-context`. |
| `sidecar_used` | `boolean` | `false`. |
| `coverage` | `object` | Same coverage contract as Repo Map JSON. |
| `path` | `string` | Session root. |
| `query` | `string` | Query text used to rank context. |
| `session_id` | `string` | Session identifier used for the cached lookup. |
| `files` | `array<string>` | Ranked source files derived from the cached repo map. |
| `symbols` | `array<object>` | Ranked symbols, including `score`. |
| `tests` | `array<string>` | Ranked tests derived from the cached repo map. |
| `related_paths` | `array<string>` | Stable union of ranked files and tests. |

## Session Serve JSONL

Emitted by `tg session serve <id> [PATH]`.

This is the long-lived session loop for repeated edit-tooling requests. It reads newline-delimited
JSON requests from stdin and emits one JSON response per line to stdout.

Request shape:

```json
{"command":"context","query":"invoice payment"}
```

Supported commands:

- `ping`
- `health`
- `stats`
- `show`
- `repo_map`
- `context`
- `context_edit_plan`
- `defs`
- `impact`
- `refs`
- `callers`
- `blast_radius`
- `blast_radius_plan`
- `blast_radius_render`

Responses reuse the same public payload shapes as the one-shot session and repo-map-derived
commands, with an added `session_id` field.

Special control-plane responses:

- `health` reports session freshness and current on-disk changes without failing the request
- `stats` reports serve-loop cache/runtime metrics including `cache_hits`, `cache_misses`,
  `refresh_count`, `root_count`, `session_count`, `sessions`, `cache_size_bytes`,
  `uptime_seconds`, and `request_count`
- routed responses also include `serve_cache` with per-request cache provenance:
  `status`, `session_count`, and `root_count`

Use `--refresh-on-stale` to refresh the cached session once and retry the request when file
changes are detected.

Invalid requests return:

```json
{"version":1,"session_id":"session-...","error":{"code":"invalid_request","message":"..."}}
```

## Failure Mode Examples

Failure-mode companion examples:

- [`examples/session_invalid_request_stale.json`](examples/session_invalid_request_stale.json): stale session request that should trigger `tg session refresh` or `--refresh-on-stale`
- [`examples/patch_bakeoff_incomplete.json`](examples/patch_bakeoff_incomplete.json): scored patch artifact with non-empty `summary.missing_predictions`; treat this as missing predictions and resume the producer before trusting aggregate scores
- [`examples/patch_bakeoff_no_patch.json`](examples/patch_bakeoff_no_patch.json): final scored failure after the producer emitted no usable patch or hit a timeout
- [`examples/defs_provider_disagreement.json`](examples/defs_provider_disagreement.json): symbol-navigation payload showing provider disagreement metadata
- [`examples/provider_status_unavailable.json`](examples/provider_status_unavailable.json): provider unavailable snapshot; treat provider disagreement or provider unavailable state as diagnostic context rather than a separate schema
- [`examples/provider_status_unavailable.json`](examples/provider_status_unavailable.json): standalone provider health snapshot showing `provider_status.last_error`; keep routing native until provider transport health is restored
- [`examples/rewrite_apply_verify_validation_failed.json`](examples/rewrite_apply_verify_validation_failed.json): apply/verify payload where edits verified byte-for-byte but post-apply validation failed and rollback metadata records restored files

These examples reuse the existing public contracts above; they are not separate schema families.

## MCP Tool Responses

The MCP server exposes stable tool contracts layered on top of the native CLI outputs.

`serverInfo.name` is `tensor-grep` and `serverInfo.version` is the stable tg MCP
server contract version (`_TG_MCP_SERVER_CONTRACT_VERSION` in `mcp_server.py`, currently
`1.2.0` -- re-check the constant before citing a version number, it has already moved once
from `1.0.0`), not the installed CLI/package version and not
the bundled MCP SDK protocol version. The initialize response top-level
`protocolVersion` is the authoritative negotiated MCP protocol for that session.
`tg_mcp_capabilities()` also exposes `mcp_protocol_version`,
`mcp_supported_protocol_versions`, and `cli_version` so agents do not infer
package or protocol compatibility from `serverInfo.version`. Stdio MCP messages
are newline-delimited JSON-RPC per the
MCP transport spec. The server also accepts legacy `Content-Length` JSON-RPC
frames defensively and still writes newline-delimited JSON-RPC responses to stdout.

PyPI wheel installs can serve simple `tg_rewrite_plan(...)` and `tg_rewrite_apply(...)` through the packaged PyO3 Rust extension even when a standalone native `tg` binary is unavailable. Rewrite diff, checkpoint, audit, validation, verify, and other native-only rewrite options still require a standalone native `tg` binary via `TG_NATIVE_TG_BINARY` or an in-tree/release build.

Call `tg_mcp_capabilities()` first when a client might be running in a PyPI wheel, sandbox, or other runtime where the standalone native binary is uncertain.

Current tool set (47 tools; re-derive with `grep -n "^def tg_\|^async def tg_" src/tensor_grep/cli/mcp_server.py | wc -l` and cross-check names against `test_harness_api_doc_lists_every_registered_tool_name`, which enumerates the live registry so this list can't silently drift again):

- `tg_mcp_capabilities()`
- `tg_rulesets()`
- `tg_ruleset_scan(ruleset=None, inline_rules=None, path=".", language=None, glob=None, file_type=None, max_depth=None, allow_broad_generated_scan=False, baseline_path=None, write_baseline=None, suppressions_path=None, write_suppressions=None, justification=None, include_evidence_snippets=False, max_evidence_snippets_per_file=1, max_evidence_snippet_chars=120)` -- exactly one of `ruleset`/`inline_rules` is required; see "Inline Rules" below.
- `tg_repo_map(path=".")`
- `tg_orient(path=".", max_tokens=3000, max_central_files=10, ignore=None)` -- call FIRST for orientation; see "Orient Capsule JSON" below.
- `tg_doctor(path=".", config="sgconfig.yml", with_lsp=True)`
- `tg_context_pack(query, path=".")`
- `tg_edit_plan(query, path=".", max_files=3, max_sources=5, max_tokens=None, max_symbols=5)`
- `tg_context_render(query, path=".", max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full")`
- `tg_agent_capsule(query, path=".", max_files=3, max_sources=5, max_tokens=1200, max_repo_files=2000, model=None, gpu_device_ids=None, gpu_timeout_s=5.0)`
- `tg_symbol_defs(symbol, path=".")`
- `tg_symbol_source(symbol, path=".")`
- `tg_symbol_impact(symbol, path=".", deadline=None)`
- `tg_symbol_refs(symbol, path=".", deadline=None)`
- `tg_symbol_callers(symbol, path=".", deadline=None)`
- `tg_symbol_blast_radius(symbol, path=".", max_depth=3, deadline=None)`
- `tg_symbol_blast_radius_plan(symbol, path=".", max_depth=3, max_files=3, max_symbols=5)`
- `tg_symbol_blast_radius_render(symbol, path=".", max_depth=3, max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full")`
- `tg_file_imports(file)`
- `tg_file_importers(file, path=".", max_repo_files=2000, deadline=None)`
- `tg_checkpoint_create(path=".")`
- `tg_checkpoint_list(path=".")`
- `tg_checkpoint_undo(checkpoint_id, path=".")`
- `tg_session_open(path=".", max_repo_files=2000)`
- `tg_session_list(path=".")`
- `tg_session_show(session_id, path=".")`
- `tg_session_refresh(session_id, path=".")`
- `tg_session_context(session_id, query, path=".", refresh_on_stale=False, auto_refresh=None)`
- `tg_session_edit_plan(session_id, query, path=".", max_files=3, max_sources=5, max_tokens=None, max_symbols=5, refresh_on_stale=False, auto_refresh=None)`
- `tg_session_context_render(session_id, query, path=".", max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full", refresh_on_stale=False, auto_refresh=None)`
- `tg_session_blast_radius(session_id, symbol, path=".", max_depth=3, refresh_on_stale=False, auto_refresh=None)`
- `tg_session_blast_radius_plan(session_id, symbol, path=".", max_depth=3, max_files=3, max_symbols=5, refresh_on_stale=False, auto_refresh=None)`
- `tg_session_blast_radius_render(session_id, symbol, path=".", max_depth=3, max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full", refresh_on_stale=False, auto_refresh=None)`
- `tg_session_file_importers(session_id, file, path=".", refresh_on_stale=False, auto_refresh=None)`
- `tg_search(pattern=None, path=".", case_sensitive=False, ignore_case=False, fixed_strings=False, word_regexp=False, context=None, max_count=None, max_results=None, max_files=None, count_matches=False, glob=None, type_filter=None, query=None, structured_json=True, max_repo_files=2000, rank=False, semantic=False)`
- `tg_ast_search(pattern, lang, path=".", structured_json=True, max_repo_files=2000)`
- `tg_index_search(pattern, path=".")`
- `tg_classify_logs(file_path, structured_json=True)`
- `tg_devices(json_output=True)`
- `tg_rewrite_plan(pattern, replacement, lang, path=".")`
- `tg_rewrite_apply(pattern, replacement, lang, path=".", verify=False, checkpoint=False, audit_manifest=None, audit_signing_key=None, lint_cmd=None, test_cmd=None, policy=None, expected_plan_digest=None, expected_match_count=None)`
- `tg_audit_manifest_verify(manifest_path, signing_key=None, previous_manifest=None)`
- `tg_audit_history(path=".")`
- `tg_audit_diff(previous_manifest, current_manifest)`
- `tg_review_bundle_create(manifest_path, scan_path=None, checkpoint_id=None, previous_manifest=None, output_path=None)`
- `tg_review_bundle_verify(bundle_path)`
- `tg_rewrite_diff(pattern, replacement, lang, path=".")`

Capability modes:

| Mode | Meaning | Representative tools |
| --- | --- | --- |
| `python-local` | Runs without a standalone native `tg` binary. | `tg_mcp_capabilities`, `tg_repo_map`, `tg_context_pack`, `tg_agent_capsule`, `tg_search`, `tg_ast_search`, `tg_devices`, `tg_checkpoint_create`, `tg_session_context` |
| `embedded-safe` | Simple requests can use packaged PyO3 rewrite fallback when standalone native `tg` is unavailable. | `tg_rewrite_plan`, `tg_rewrite_apply` |
| `native-required` | Requires a standalone native `tg` binary via PATH, `TG_NATIVE_TG_BINARY`, in-tree build, or release asset. | `tg_index_search`, `tg_rewrite_diff` |

`tg_mcp_capabilities()` response fields:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `MCPRuntime`. |
| `routing_reason` | `string` | `mcp-capabilities`. |
| `sidecar_used` | `boolean` | `false`. |
| `mcp_protocol_version` | `string` | Latest MCP protocol version exposed by the server runtime; initialize `protocolVersion` remains authoritative for the active session. |
| `mcp_supported_protocol_versions` | `array<string>` | MCP protocol versions accepted by the server runtime. |
| `cli_version` | `string` | Installed `tensor-grep` package/server implementation version. |
| `native_tg.available` | `boolean` | True when a standalone native `tg` binary was resolved. |
| `native_tg.path` | `string \| null` | Resolved native binary path when available. |
| `embedded_rewrite.available` | `boolean` | True when packaged rewrite plan/apply fallback is importable. |
| `tools[].name` | `string` | Public MCP tool name. |
| `tools[].mode` | `string` | One of `python-local`, `embedded-safe`, or `native-required`. |
| `tools[].native_required` | `boolean` | True for tools that cannot run without standalone native `tg`. |
| `tools[].embedded_fallback` | `boolean` | True for tools with simple embedded rewrite fallback. |
| `tools[].native_required_options` | `array<string>` | Options that make an otherwise embedded-safe tool require standalone native `tg`; for `tg_rewrite_apply`, this includes `verify`, `audit_manifest`, `audit_signing_key`, `lint_cmd`, and `test_cmd`. Checkpointed apply can use the embedded fallback. |
| `tools[].notes` | `string` | Human-readable routing note. |

Native-unavailable error responses use `error.code = "unavailable"`, `routing_reason = "native-tg-unavailable"`, include the `tool` name, and include `error.remediation` with `TG_NATIVE_TG_BINARY` guidance.

Response mapping:

- `tg_mcp_capabilities()` returns the MCP runtime capability envelope described above
- `tg_rulesets()` returns the same v1 envelope and payload shape as [`examples/rulesets.json`](examples/rulesets.json)
- `tg_ruleset_scan(...)` returns the same v1 envelope and payload shape as [`examples/ruleset_scan.json`](examples/ruleset_scan.json) for a built-in `ruleset`, or the same shape with `routing_reason = "ast-inline-rules-scan"` and `ruleset = null` for `inline_rules` -- see "Inline Rules" under Ruleset Scan JSON above
- `tg_orient(...)` returns the same shape as `tg.exe orient --json` -- see "Orient Capsule JSON" above
- `tg_doctor(...)` returns the same v2 doctor schema as `tg.exe doctor --json` -- see "Doctor JSON" above
- `tg_index_search(...)` returns the same v1 envelope and payload shape as [`examples/index_search.json`](examples/index_search.json)
- `tg_edit_plan(...)` returns the same v1 envelope and payload shape as [`examples/edit_plan.json`](examples/edit_plan.json)
- `tg_context_render(...)` returns the same v1 envelope and payload shape as [`examples/context_render.json`](examples/context_render.json)
- `tg_agent_capsule(...)` returns the same v1 capsule contract as `tg.exe agent <path> <query> --json`, with `routing_reason = "agent-context-capsule"`. Optional `gpu_device_ids` request native GPU evidence and populate `gpu_acceleration`; sidecar-routed GPU evidence is reported as unsupported.
- `tg_symbol_defs(...)` returns the same v1 envelope and payload shape as [`examples/defs.json`](examples/defs.json)
- `tg_symbol_source(...)` returns the same v1 envelope and payload shape as [`examples/source.json`](examples/source.json)
- `tg_symbol_impact(...)` returns the same v1 envelope and payload shape as [`examples/impact.json`](examples/impact.json)
- `tg_symbol_refs(...)` returns the same v1 envelope and payload shape as [`examples/refs.json`](examples/refs.json)
- `tg_symbol_callers(...)` returns the same v1 envelope and payload shape as [`examples/callers.json`](examples/callers.json)
- `tg_symbol_blast_radius(...)` returns the same v1 envelope and payload shape as [`examples/blast_radius.json`](examples/blast_radius.json)
- `tg_symbol_blast_radius_plan(...)` returns the same v1 envelope and payload shape as [`examples/blast_radius_plan.json`](examples/blast_radius_plan.json)
- `tg_symbol_blast_radius_render(...)` returns the same v1 envelope and payload shape as [`examples/blast_radius_render.json`](examples/blast_radius_render.json)
- `tg_session_open(...)` returns the same payload shape as [`examples/session_open.json`](examples/session_open.json)
- `tg_session_refresh(...)` returns the same payload shape as Session Refresh JSON
- `tg_session_context(...)` returns the same payload shape as [`examples/session_context.json`](examples/session_context.json)
- `tg_session_edit_plan(...)` returns the same payload shape as [`examples/edit_plan.json`](examples/edit_plan.json) plus `session_id` and `routing_reason = "session-context-edit-plan"`
- `tg_session_context_render(...)` returns the same payload shape as [`examples/context_render.json`](examples/context_render.json) plus `session_id` and `routing_reason = "session-context-render"`
- `tg_session_blast_radius(...)` returns the same payload shape as [`examples/blast_radius.json`](examples/blast_radius.json) plus `session_id` and `routing_reason = "session-blast-radius"`
- `tg_session_blast_radius_plan(...)` returns the same payload shape as [`examples/blast_radius_plan.json`](examples/blast_radius_plan.json) plus `session_id` and `routing_reason = "session-blast-radius-plan"`
- `tg_session_blast_radius_render(...)` returns the same payload shape as [`examples/blast_radius_render.json`](examples/blast_radius_render.json) plus `session_id` and `routing_reason = "session-blast-radius-render"`
- `tg_rewrite_plan(...)` returns the same v1 envelope and payload shape as [`examples/rewrite_plan.json`](examples/rewrite_plan.json)
- `tg_rewrite_apply(..., verify=True, checkpoint=True, audit_manifest=..., audit_signing_key=..., lint_cmd=..., test_cmd=..., policy=...)` returns the same v1 envelope and payload shape as [`examples/rewrite_apply_verify.json`](examples/rewrite_apply_verify.json)
- `tg_audit_manifest_verify(...)` returns the same v1 envelope and payload shape as [`examples/audit_manifest_verify.json`](examples/audit_manifest_verify.json)
- `tg_rewrite_diff(...)` returns a diff wrapper JSON object instead of raw diff text

Example diff wrapper: [`examples/mcp_rewrite_diff.json`](examples/mcp_rewrite_diff.json)

`tg_rewrite_diff(...)` response fields:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | `AstBackend`. |
| `routing_reason` | `string` | `ast-native`. |
| `sidecar_used` | `boolean` | `false`. |
| `diff` | `string` | Unified diff preview generated by the native CLI. |

Native-required MCP tools return this unavailable shape when standalone native `tg` cannot be resolved:

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `integer` | Contract version. |
| `routing_backend` | `string` | Existing backend family, such as `AstBackend` or `TrigramIndex`. |
| `routing_reason` | `string` | `native-tg-unavailable`. |
| `sidecar_used` | `boolean` | `false`. |
| `tool` | `string` | MCP tool that could not run. |
| `error.code` | `string` | `unavailable`. |
| `error.message` | `string` | Native requirement summary. |
| `error.remediation` | `string` | Install native `tg`, expose it on PATH, or set `TG_NATIVE_TG_BINARY`. |

## Rust vs Python field differences

The current codebase still exposes a few shape differences between native Rust JSON and Python-originated JSON:

| Area | Native Rust output | Python-originated output |
| --- | --- | --- |
| Match line field | `line` | `line_number` |
| Search metadata | `query`, `path`, `total_matches`, `requested_gpu_device_ids`, `routing_gpu_device_ids` | Python CLI/search sidecar payloads may also include `total_files`, `matched_file_paths`, `match_counts_by_file`, and GPU worker metadata |
| GPU search envelope | Rust adds `version`, `routing_backend`, `routing_reason`, `sidecar_used`, and caller-requested GPU IDs | Python provides the nested match payload and routed GPU IDs that Rust augments rather than reshaping |

In practice:

- `search.json` and `index_search.json` are fully native Rust shapes.
- `rewrite_plan.json` and `rewrite_apply_verify.json` are fully native Rust shapes.
- `gpu_sidecar_search.json` is the mixed Rust/Python shape, so its match rows use `line_number`, not `line`.

## Diff Output

`tg.exe run --rewrite ... --diff ...` does **not** emit JSON. It prints a unified diff preview and does not modify the file on disk.

Expected structure:

- `--- a/<path>` original file header
- `+++ b/<path>` rewritten file header
- `@@ -old,+new @@` hunk header
- removed lines prefixed with `-`
- added lines prefixed with `+`

Example excerpt from a real run:

```diff
--- a/C:\dev\projects\tensor-grep\bench_data\harness_api_doc_inputs\rewrite\rewrite_fixture.py
+++ b/C:\dev\projects\tensor-grep\bench_data\harness_api_doc_inputs\rewrite\rewrite_fixture.py
@@ -1,2 +1,2 @@
-def add(x, y): return x + y
-def mul(a, b): return a * b
+lambda x, y: x + y
+lambda a, b: a * b
```

## Command summary

The committed examples were generated with commands equivalent to:

```powershell
tg.exe search --no-ignore --json ERROR bench_data\<temp-search-dir>
tg.exe search --index --no-ignore --fixed-strings --json ERROR bench_data\<temp-index-dir>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --apply --verify --lint-cmd "ruff check ." --test-cmd "pytest -q" --json 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
tg.exe run --batch-rewrite batch-rewrite.json --apply --json bench_data\<temp-rewrite-dir>
tg.exe search --gpu-device-ids 0 --json ERROR bench_data\<temp-gpu-dir>
tg.exe calibrate
tg.exe search --no-ignore --ndjson ERROR bench_data\<temp-search-dir>
tg.exe run --lang python --rewrite 'lambda $$$ARGS: $EXPR' --diff 'def $F($$$ARGS): return $EXPR' bench_data\<temp-rewrite-file>
```

## Compatibility Policy

The harness API is a versioned public contract.

Rules:

- additive field changes are allowed within the same major contract version when existing required fields and meanings stay intact
- breaking changes require a version bump
- field renames, type changes, removing required fields, or changing single-document output into a different transport shape are breaking changes
- new example artifacts and schema tests must land with any contract expansion
- docs, example artifacts, and schema tests must stay in sync





