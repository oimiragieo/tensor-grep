---
name: tensor-grep
description: Search a repository, inspect symbols and callers, and prepare a bounded edit investigation with tensor-grep.
---

# Use tensor-grep

Use `tg` to find relevant files and inspect the evidence before editing. Start with
`tg --version` and `tg --help`; installed entry points and optional dependencies
affect available features.

This guidance was checked against v1.125.0. Native `--version` prints `tg X.Y.Z`;
the Python launcher prints `tensor-grep X.Y.Z`. Record the artifact and package
origins separately. MCP's `serverInfo.version` is its contract version (1.16.0),
not the installed CLI version; call `tg_mcp_capabilities` for both values.

## Choose a command

```text
tg orient REPO_PATH --json
tg search 'pattern' REPO_PATH --rank --json
tg source REPO_PATH SYMBOL
tg callers REPO_PATH SYMBOL --json
tg prepare REPO_PATH 'task description' --json
tg file-api REPO_PATH/src/example.py --json
tg freshness REPO_PATH --json
tg sql REPO_PATH --query-file query.sql --json
tg dogfood --all --json
tg agent REPO_PATH 'investigate symbol' --plan-hops --grounding local --json
tg find 'task description' REPO_PATH --grounding off --rerank auto --json
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

For `tg scan`, inspect `partial` and `partial_reason` even when the command
exits `0`. Backend-reported skipped input is disclosed in JSON/MCP and text;
SARIF marks the invocation unsuccessful. Partial findings do not prove the
remaining scope is clean.

Malformed regex output can suggest fixed-string search. If the query is literal,
retry with `-F`; the hint does not automatically change regex semantics.

With `TG_RRF_SYMBOLS=1`, `tg search --rank` and `tg find` can combine lexical
ranking with parser-backed exact declaration evidence. Inspect `rank_fusion`
when supplied and its completeness/fallback
metadata. Dense ranking is optional; a disclosed BM25 fallback is useful lexical
output, not semantic-model execution.

Use `tg context-render REPO_PATH 'query' --render-profile focused --json` for
shorter declaration excerpts. Inspect `omitted_sections`, `truncated`, and line
maps, then read the full source before editing. Focused excerpts do not establish
data-flow completeness; `tg agent` does not accept `--render-profile`.

A command deadline may stop new work without imposing a hard process timeout.
Automation should impose its own external timeout.

`tg find` and `tg agent` accept `--grounding off|local|registry` (default local).
Inspect dependency constraints, lockfile provenance, omissions, and API evidence;
registry mode explicitly requests bounded network metadata and never proves API compatibility.
Use `--grounding registry` only when registry requests and receipt cache writes are intended.
`tg agent --plan-hops` reuses existing evidence for up to three investigation stages.
Preserve its ambiguity and partial status before choosing a target. See
[investigation evidence](../../../docs/investigation.md) for the supported metadata slice.
Native cross-encoder ordering is optional: explicitly install its assets using
`tg install-dense --reranker`, then select `--rerank cross-encoder` to require scoring.
`--rerank auto` discloses unavailable assets and retains the prior ordering.

## Prepare and verify changes

Use returned validation suggestions as a starting point, then run checks appropriate
to the actual diff. Review proposed edits and checkpoint scope before applying them.
`tg prepare REPO_PATH 'task description' --out capsule.json --json` persists the
capsule; `--claim` also submits its advisory coordination claim. Check validation
runner evidence and `ask_user_before_editing` before choosing a target.
For already-authorized, reversible edits in non-interactive automation, do not ask for confirmation again: make the change directly and report the result instead of
ending with "want me to apply this?". This does not authorize destructive operations.

Use [REFERENCE.md](REFERENCE.md) for sessions, checkpoints, and structural search.
For response contracts and optional features, read
[the harness API](../../../docs/harness_api.md) and
[experimental features](../../../docs/EXPERIMENTAL.md).
