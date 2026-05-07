# API and Data Contracts

This document defines the backward-compatibility guarantees for data structures and CLI outputs used by enterprise integrations, IDEs, and editor-plane agents.

## 1. Configuration (`sgconfig.yml`)
The root-level keys and structure of `sgconfig.yml` are guaranteed to be stable within a major version. Unrecognized keys will be ignored rather than causing fatal errors to allow progressive rollout of new configurations.

## 2. AST Cache (`project_data_v6.json`)
The schema of the `.tg_cache/ast/project_data_v6.json` cache is versioned within its filename.
- Backward compatibility is NOT guaranteed across major/minor versions.
- If the schema changes (for example to `v7`), `tensor-grep` will automatically invalidate older cache files and rebuild them transparently.

## 3. Text-search compatibility
The stable text-search compatibility contract is the validated compatibility set covered by the parity suite and contract benchmark runner. `tg search --help` may expose more rg-style flags, but only the rows below and explicitly named deterministic edges are part of the public rg-compatibility claim.

Current validated rows:
- `-i/--ignore-case`
- `-v/--invert-match`
- `-C/--context`
- `-A/--after-context`
- `-B/--before-context`
- `-g/--glob`
- `-l/--files-with-matches`
- `--files-without-match`
- `--json`
- `--ndjson`
- `-F/--fixed-strings`
- `-w/--word-regexp`
- `-m/--max-count`
- `-t/--type`
- `-./--hidden`
- `-L/--follow`
- `-S/--smart-case`
- `-n/--line-number`
- `--column`
- `-c/--count`
- `--count-matches`
- `-a/--text`

Character-for-character identity is not required for help formatting, but command presence, supported rows, accepted normalization, and the deterministic parity corpus are part of the public contract. Additional rg-style flags may be exposed in `tg search --help`, but they are not covered by the stable compatibility claim until they are added to the contract suite and benchmark runner.

Agent automation contracts:
- `tg search PATTERN` defaults to the current directory when no path is provided.
- Invalid regex syntax exits as an error distinct from no-match and emits a diagnostic that recommends `--fixed-strings` for literal searches.
- `tg search --json` emits a valid aggregate JSON object even when there are zero matches.
- `tg search --files-with-matches` stays root-based on the ripgrep path instead of expanding large candidate-file lists into the Windows process argument vector, and plain path-list output emits one trailing line separator only.
- `tg search` with `--format rg` is a public exact ripgrep-style text formatter for automation that needs rg-shaped stdout. Pair it with `--sort path` when deterministic path ordering matters across backends.
- `--sort path` is the deterministic contract for `--files-with-matches`, `--files-without-match`, `--replace`, and path-list automation. The parity suite also checks match, no-match, parse-error, and binary-skip exit-code behavior.
- Default output ordering for root-scale `--files-with-matches`, `--count`, and `--force-cpu` is a semantic result parity contract, not a golden stdout ordering contract. Use `--sort path` when automation needs deterministic path ordering across backends.
- Broad generated roots such as `.claude` are routed through Python guardrails instead of rust-first/native passthrough so generated `.claude/context` snapshots can be pruned by default.
- `tg search --type-list` prints a built-in fallback list when no ripgrep or standalone native binary is available. `--pcre2-version` follows ripgrep semantics and exits with an error when no PCRE2-capable backend is available.
- `tg ast-info --json` exposes AST language identifiers via `{"languages": [...]}` for agent discovery without help-text scraping.
- On Windows, PowerShell automation should invoke `tg` or `tg.ps1` for regex metacharacters. Direct `.cmd` invocation from PowerShell is not a safe argv-preserving contract for unescaped metacharacters such as `|` because `cmd.exe` parses the line before the batch wrapper receives arguments; `tg.cmd` is for `cmd.exe`.
- The fast agent-readiness dogfood gate is `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`. For the current `v1.8.22` line it checks public shell version probes, repo doctor sanity, `context_consistency`, deterministic rg parity edges, AST smoke, MCP context-render smoke, docs claim hygiene, and the public positioning that `rg` remains the cold exact-text baseline while `ast-grep` remains the structural-search feature/performance baseline.

Context and edit-planning contracts:
- `context-render` and MCP context output must not let `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, rendered source sections, and follow-up reads contradict each other.
- `context_consistency` reports whether the primary file is included, whether rendered context matches the primary target, whether confidence was downgraded, and why a primary file was omitted from rendered budget.
- The default JSON/LLM render profile includes executable body lines for selected functions. Compact rendering can remove comments, docstrings, blank lines, type-only imports, and boilerplate, but it must preserve matched behavior unless the caller requests a summary-only profile in a future contract.
- Validation command hints use `validation_plan[].detection` with values of `detected`, `heuristic`, or `generic`. JavaScript package-manager commands require `package.json` evidence; Python commands require tests, project markers, or Python layout evidence; omit commands entirely when no runner evidence exists instead of inventing `npm test` or `uv run pytest`.
- Repo-map and context-ranking defaults exclude generated/cache/dependency directories, binary files, logs, and hidden non-code files from normal code context.

Known current limitations:
- Broad plain path-list output from `tg search --files ...` over generated artifact trees can still be expensive. The Python path-list output path and managed Windows launchers force UTF-8, but scope file-list commands to the smallest useful root.
- Broad generated roots can still be expensive for unattended agents. Use scoped paths, globs, file types, and `--max-depth` for `tg search`; `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `impact --symbol` is a broader planning signal and can be noisier than `blast-radius`; use `blast-radius` for direct symbol impact checks.

## 4. Machine-readable CLI output (`--json` and `--ndjson`)
The JSON schemas emitted by `tensor-grep search --json`, `tensor-grep search --ndjson`, and the documented harness/editor-plane flows are considered public APIs.
- Existing fields (for example `file`, `line`, `match`, `context`) will not be renamed or removed without a major version bump.
- New fields may be added in minor versions.
- Consumers should ignore unrecognized JSON fields.

## 5. Operational diagnostics (`tg doctor --json`)
`tg doctor --json` is intended for operational automation and support workflows.
- Existing top-level sections remain additive-only within a major version.
- Individual diagnostic fields may grow as new probes are added.
- Consumers should treat missing optional fields as a valid state and ignore unknown fields.
- Implicit native-binary resolution must not select stale in-tree `rust_core/target/*/tg(.exe)` binaries. Stale in-tree candidates should be reported under `skipped_native_tg_binaries` with `rust_binary_version_status = stale-skipped`; `TG_NATIVE_TG_BINARY` is the explicit opt-in for using a specific standalone binary anyway.

## 6. Python Library API
Classes and functions exposed in `tensor_grep.api` are stable within a major version. Internal modules (prefixed with `_` or deep inside `tensor_grep.core`, `tensor_grep.cli`, or backend-specific packages) are subject to change without notice.

## 7. Explicitly unstable / experimental surface
The items documented in [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) are not covered by the stability guarantees in this file.
- Hidden commands such as `tg worker`
- Opt-in runtime flags such as `TG_RESIDENT_AST`
- Temporary backend override environment variables used during migration or benchmarking

These surfaces may change, move, or be removed in minor releases.
