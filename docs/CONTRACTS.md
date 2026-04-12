# API and Data Contracts

This document defines the backward-compatibility guarantees for internal structures and CLI outputs used by enterprise integrations, IDEs, and editor-plane agents.

## 1. Configuration (`sgconfig.yml`)
The root-level keys and structure of `sgconfig.yml` are guaranteed to be stable within a major version. Unrecognized keys will be ignored rather than causing fatal errors to allow progressive rollout of new configurations.

## 2. AST Cache (`project_data_v6.json`)
The schema of the `.tg_cache/ast/project_data_v6.json` cache is versioned within its filename.
- Backward compatibility is NOT guaranteed across major/minor versions.
- If the schema changes (e.g., to `v7`), `tensor-grep` will automatically invalidate older cache files and rebuild them transparently.

## 3. CLI Output (`--json` and `--ndjson`)
The JSON schemas emitted by `tensor-grep search --json` and related commands are considered public APIs.
- Existing fields (e.g., `file`, `line`, `match`, `context`) will not be renamed or removed without a major version bump.
- New fields may be added in minor versions.
- Consumers should ignore unrecognized JSON fields.

## 4. Python Library API
Classes and functions exposed in `tensor_grep.api` are stable. Internal modules (prefixed with `_` or deep inside `tensor_grep.core` or `backends`) are subject to change without notice.
