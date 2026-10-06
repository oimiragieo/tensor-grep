---
name: tensor-grep
description: Search a repository, inspect symbols and callers, and prepare a bounded edit investigation with tensor-grep.
---

# Use tensor-grep

Use `tg` to find relevant files and inspect the evidence before editing. Start with
`tg --version` and `tg --help`; installed entry points and optional dependencies
affect available features.

## Choose a command

```text
tg orient REPO_PATH --json
tg search 'pattern' REPO_PATH --rank --json
tg source REPO_PATH SYMBOL
tg callers REPO_PATH SYMBOL --json
tg prepare REPO_PATH 'task description' --json
```

Symbol commands use path-first order. Search takes the pattern before the path.
Pass an explicit project or source directory; narrow large scans to a subtree.
Quote structural patterns containing `$NAME`, especially in PowerShell.

Inspect the selected file and line before making a change. Ranking identifies
candidates; it does not establish correctness. A zero-caller result is not proof
of dead code: callbacks, decorators, dispatch tables, and dynamic imports may not
be resolved.

## Interpret bounded output

Check `result_incomplete` for incomplete analysis. Separately, `output_limit`
describes display-only omissions such as caller, file, test-file, and import-consumer
counts. Text output prints `OUTPUT LIMITED` on stdout for this condition.
Increase the corresponding output limit when needed; do not treat an omitted
entry as evidence of absence.

A command deadline may stop new work without imposing a hard process timeout.
Automation should impose its own external timeout.

## Prepare and verify changes

Use returned validation suggestions as a starting point, then run checks appropriate
to the actual diff. Review proposed edits and checkpoint scope before applying them.
For already-authorized, reversible edits in non-interactive automation, do not ask for confirmation again: make the change directly and report the result instead of
ending with "want me to apply this?". This does not authorize destructive operations.

Use [REFERENCE.md](REFERENCE.md) for sessions, checkpoints, and structural search.
For response contracts and optional features, read
[the harness API](../../../docs/harness_api.md) and
[experimental features](../../../docs/EXPERIMENTAL.md).
