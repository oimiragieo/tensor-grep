# API and Data Contracts

This document defines the backward-compatibility guarantees for data structures and CLI outputs used by enterprise integrations, IDEs, and editor-plane agents.

## 1. Configuration (`sgconfig.yml`)
The root-level keys and structure of `sgconfig.yml` are guaranteed to be stable within a major version. Unrecognized keys will be ignored rather than causing fatal errors to allow progressive rollout of new configurations.

## 2. AST Cache (`project_data_v6.json`)
The schema of the `.tg_cache/ast/project_data_v6.json` cache is versioned within its filename.
- Backward compatibility is NOT guaranteed across major/minor versions.
- If the schema changes (for example to `v7`), `tensor-grep` will automatically invalidate older cache files and rebuild them transparently.

## 3. Text-search compatibility
The stable text-search compatibility contract is the validated rg-compatible surface covered by the parity suite and contract benchmark runner.

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

## 6. Python Library API
Classes and functions exposed in `tensor_grep.api` are stable within a major version. Internal modules (prefixed with `_` or deep inside `tensor_grep.core`, `tensor_grep.cli`, or backend-specific packages) are subject to change without notice.

## 7. Explicitly unstable / experimental surface
The items documented in [docs/EXPERIMENTAL.md](EXPERIMENTAL.md) are not covered by the stability guarantees in this file.
- Hidden commands such as `tg worker`
- Opt-in runtime flags such as `TG_RESIDENT_AST`
- Temporary backend override environment variables used during migration or benchmarking

These surfaces may change, move, or be removed in minor releases.
