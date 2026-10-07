# Tensor-grep command reference

Replace `REPO_PATH`, `SYMBOL`, and returned IDs with actual values. Examples
assume the command is available from your installation.

## Search and inspect

```text
tg search -F -n 'ERROR' REPO_PATH
tg search 'pattern' REPO_PATH --rank --json
tg source REPO_PATH SYMBOL
tg defs REPO_PATH SYMBOL --json
tg refs REPO_PATH SYMBOL --json
tg callers REPO_PATH SYMBOL --json
tg blast-radius REPO_PATH SYMBOL --json
tg orient REPO_PATH --json
tg prepare REPO_PATH 'task description' --json
tg file-api REPO_PATH/src/example.py --json
tg find 'task description' REPO_PATH --why-ranked --json
```

Text search returns 0 for matches, 1 for no matches, and 2 for an error or
incomplete result. Other commands can have different exit-code contracts.
Inspect completeness and output-limit fields in machine-readable responses.
`tg find` is experimental and can disclose BM25-only operation when dense
dependencies or a model are absent. It downloads nothing automatically.

## Structural search

```text
tg run -p 'print($VALUE)' --lang python REPO_PATH
```

For Python package installations, `python -m pip install "tensor-grep[ast,scan]"`
adds optional parsers and the ast-grep command. Structural-search support is a
validated subset; consult the installed help before using additional flags.
Use `tg rulesets --json` to discover built-in packs and
`tg scan --ruleset secrets-basic --path REPO_PATH/src --json` to scan a scoped
directory. `--sarif` emits SARIF; baseline and suppression files are separate
explicit inputs/outputs. A zero-finding or exit-0 scan is not proof of completeness:
inspect `partial`, unreadable paths, and SARIF execution status.

## Sessions

```text
tg session open REPO_PATH --json
tg session context SESSION_ID REPO_PATH 'task description' --json
tg session refresh SESSION_ID REPO_PATH --json
tg session context SESSION_ID REPO_PATH 'task description' --refresh-on-stale --json
tg freshness REPO_PATH --json
```

Inspect the returned root: project detection may select an ancestor directory.
Use that root consistently. Refresh the session after changing project files.
Cold freshness exits 2 with `incomplete_reason=no_persisted_state`; open a
session before checking whether cached state is current. `--refresh-on-stale`
can refresh a stale request and retry. `tg session daemon start/status/stop`
manages a warm local service, and `tg session serve` accepts JSONL requests.

## Checkpoints

```text
tg checkpoint create REPO_PATH --paths src/example.py --label 'Before refactor' --json
tg checkpoint list REPO_PATH --json
tg checkpoint undo CHECKPOINT_ID REPO_PATH --json
```

Confirm the saved scope and returned undo command before restoring. Undo can
discard later edits. Labels are optional descriptive text, trimmed to 1–120
printable Unicode characters; duplicate labels are allowed. Restore by checkpoint
ID, not by label. Checkpoints are recovery material, not disposable search caches.
Use the returned checkpoint root and ID, especially after a rewrite scoped to
a single file. `undo --last` selects the newest checkpoint for the supplied
root; it does not imply the newest checkpoint in every descendant store.

## Evidence and coordination

```text
tg evidence emit REPO_PATH --capsule capsule.json --out receipt.json --json
tg evidence verify receipt.json --json
tg ledger claim REPO_PATH --symbol SYMBOL --json
tg ledger list REPO_PATH --json
tg ledger release REPO_PATH --claim-id CLAIM_ID --json
tg ledger record REPO_PATH --receipt receipt.json --symbol SYMBOL --json
tg ledger find REPO_PATH --symbol SYMBOL --json
```

Ledger coordination and artifact reuse are experimental and advisory. They do
not establish exclusive edit ownership or automatically validate a change.
Evidence digests establish integrity; signatures require explicit signing keys.
Use `--require-trusted` with pinned public keys when enforcing signer trust.

## Diagnostics and MCP

```text
tg doctor --json
tg mcp
```

Read reported capabilities rather than assuming a GPU or parser is available.
A successful Python sidecar or CPU fallback is not proof of native GPU execution.
MCP exposes structured tool calls; client discovery and the
[harness API](../../../docs/harness_api.md) define the interface.
Start with capabilities and live tool schemas. `tg_query` accepts `text` and
`search` aliases; provide scoped paths or explicit workspace roots. Requests
outside the configured MCP root are refused. On Windows, use the native
`tg.exe` directly for stdio clients that launch a subprocess, with its required
Python sidecar installed and available; verify this capability with `doctor`.

`tg dogfood --output REPORT_PATH --no-wsl-probe` runs repository readiness
checks when their source harness is available. The post-release smoke script
does not cover every feature; record individual checks and unavailable features.
