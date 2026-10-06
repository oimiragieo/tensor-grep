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
```

Text search returns 0 for matches, 1 for no matches, and 2 for an error or
incomplete result. Other commands can have different exit-code contracts.
Inspect completeness and output-limit fields in machine-readable responses.

## Structural search

```text
tg run -p 'print($VALUE)' --lang python REPO_PATH
```

For Python package installations, `python -m pip install "tensor-grep[ast,scan]"`
adds optional parsers and the ast-grep command. Structural-search support is a
validated subset; consult the installed help before using additional flags.

## Sessions

```text
tg session open REPO_PATH --json
tg session context SESSION_ID REPO_PATH 'task description' --json
tg session refresh SESSION_ID REPO_PATH --json
```

Inspect the returned root: project detection may select an ancestor directory.
Use that root consistently. Refresh the session after changing project files.

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

## Diagnostics and MCP

```text
tg doctor --json
tg mcp
```

Read reported capabilities rather than assuming a GPU or parser is available.
A successful Python sidecar or CPU fallback is not proof of native GPU execution.
MCP exposes structured tool calls; client discovery and the
[harness API](../../../docs/harness_api.md) define the interface.
