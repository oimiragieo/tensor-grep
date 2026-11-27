# Getting started

This guide gets you from installation to a first search, then shows which command fits common code tasks. It uses a tiny project you create yourself, so you do not need a particular repository or an API key.

## Words used in this guide

- A **terminal** is a text window where you type commands. PowerShell is the usual terminal on Windows; Terminal on macOS and a shell such as Bash on Linux serve the same role.
- A **current directory** is the folder a terminal uses when a command names a relative path such as `./logs`. `.` means the current folder.
- A **path** names a file or folder. `./logs` means the `logs` folder under the current directory.
- A **pattern** is the text or expression to look for. Quote it so the shell passes it as one argument.
- A **literal** pattern means “these exact characters.” A **regular expression** (regex) gives characters special pattern meanings.
- A **line number** identifies a line within a file. It is useful when you open a search result in an editor.
- A **read-only search** reports matches and leaves the searched files unchanged.

## Install and check the command

Follow [installation](installation.md) to install `tg`. In a new terminal, run:

```text
tg --version
tg --help
```

The first command prints the installed version. The second shows the **12 core workflow commands** (`search`, `find`, `run`, `scan`, `orient`, `defs`, `callers`, `blast-radius`, `prepare`, `diff-impact`, `checkpoint`, `mcp`). Run `tg --help-all` when you want to see every command in the full catalog. Python package installs and managed native installs can expose different implementation paths; [architecture](architecture.md) explains that distinction.

Most planning and orientation commands (`tg orient`, `tg prepare`, `tg edit-plan`) default to concise human-readable plain text; add `--json` when a script needs structured output, or `--json --compact` for a bounded `<= 4,000`-byte JSON payload.

## Create a small practice project

Choose a temporary folder outside any important project. Create this layout and save the shown text in each file:

```text
practice/
  logs/app.log
  src/invoice.py
```

`logs/app.log`:

```text
INFO application started
ERROR invoice total is missing
INFO application stopped
```

`src/invoice.py`:

```python
def invoice_total(amount, tax):
    return amount + tax


print(invoice_total(100, 5))
```

Open a terminal in `practice`. The examples below use paths relative to that folder.

## Search a sample log

Search for the exact word `ERROR`:

```text
tg search -F -n 'ERROR' ./logs
```

You should see `app.log`, line `2`, and `ERROR invoice total is missing`. `-F` asks for a fixed (literal) string; `-n` includes line numbers. Search does not edit the log.

This command also asks for a stable path-oriented output format:

```text
tg search --format rg --sort path -F -n 'ERROR' ./logs
```

To check a missing word, run:

```text
tg search -F 'NOT_PRESENT' ./logs
```

For the documented text-search contract, exit code `0` means matches were found, `1` means no match, and `2` indicates an error or incomplete result. Other commands may use different exit codes. In PowerShell, `$LASTEXITCODE` contains the most recent native command's exit code; read it before running another command.

## Search with a regular expression or file type

Without `-F`, the search pattern is treated as a regex. For example, `ERROR|WARN` matches either word:

```text
tg search -n 'ERROR|WARN' ./logs
```

The quotes keep the pattern together when it contains spaces or punctuation. In PowerShell, single quotes also prevent `$` in a regex from being expanded as a variable. To search only Python files, use the file-type filter:

```text
tg search -t py 'def invoice_total' ./src
```

Use [the text-search reference](harness_api.md) for supported flags and output formats. `tg` supports a validated subset of ripgrep behavior; for the full ripgrep feature set, use `rg` directly.

## Search code structure

Text search finds characters. Structural search parses code and can match a shape such as “a call whose callee is `print`,” regardless of the argument expression. An **abstract syntax tree (AST)** is the parser's tree-shaped representation of code.

Try the supported AST search slice:

If you installed the Python package, first add its structural-search dependency:

```text
python -m pip install "tensor-grep[ast,scan]"
```

This installs the ast-grep command used by the Python path for patterns with named
wildcards, plus optional language parsers for the code-analysis tasks later in this guide.
The native binary supports the following structural-search example directly.

```text
tg run -p 'print($VALUE)' --lang python ./src
```

`$VALUE` is a named wildcard: it stands for an expression in the call. The output should identify the `print(invoice_total(100, 5))` call. This is a useful validated slice of structural search, not a replacement for every feature in ast-grep. Parser support and configuration affect which files can be analyzed. See the [harness API](harness_api.md) for command output details.

## Choose a next task

| If you need to… | Start with… | What it gives you |
|---|---|---|
| Find text in a file tree | `tg search` | Matching lines, paths, and optional line numbers |
| Find a code shape | `tg run` | Structural matches using a supported language parser |
| Get a quick map of likely central files | `tg orient ./src --max-tokens 1000 --json` | A bounded orientation result; its ranking is a heuristic |
| Inspect one file's public signatures | `tg file-api ./src/invoice.py --json` | Function, class, and TypeScript `interface`/`type`/`enum` signatures without reading file bodies |
| Follow a symbol or its callers | `tg callers ./src invoice_total --json --detail compact` | Call sites and affected tests; `--detail compact` omits verbose diagnostics while keeping completeness flags |
| Gather files relevant to a task | `tg context ./src 'calculate an invoice total' --max-tokens 1000 --json` | A bounded context result based on local project data |
| Get a compact edit plan under 4 KB | `tg edit-plan ./src 'add invoice tax field' --json --compact` | A compact JSON plan guaranteed to stay within 4,000 bytes while keeping key safety fields |
| Search by keywords and relevance | `tg find --hybrid 'invoice total' ./src --json` | Local-first keyword + relevance ranking (`SQLite FTS5` + RRF) with no API key or GPU |
| Prepare an edit investigation | `tg prepare ./src 'add invoice tax field' --json` | Suggested target and supporting context for review, not approval to edit |
| Check your local installation | `tg doctor . --fast --json` | Fast launcher, backend, and environment health report without slow network or LSP probes |
| Measure blast radius of your git diff | `tg diff-impact . --json` | Modified symbols, affected callers/tests, KG-AST risk score, and a minimal change slice |

If a literal pattern starts with `-` (for example `--compact`), place `--` before the pattern so `tg search` does not treat it as a flag: `tg search -F -- '--compact' ./src`.

**JSON** is a text format for structured data. The `--json` options make results easier for programs to read; a person can still inspect them. A **context capsule** is a bounded package of selected files and metadata for a task. Limits such as `--max-tokens` constrain output size, not correctness. Caller and impact reports may be partial when a scan reaches its configured bounds; inspect their completeness fields before treating missing results as proof.

## Investigate a change step by step

When you are asked to fix a bug or add a small feature in an unfamiliar folder, follow this four-step read-only loop before editing any code:

1. **Get your bearings without reading every file.**

   ```text
   tg orient ./src --json --compact
   tg file-api ./src/invoice.py --json
   ```

   `tg orient --compact` returns a bounded JSON summary (`<= 4,000` bytes) of central files and entry points. `tg file-api` shows only function and class signatures (`invoice_total(amount, tax)`) so you can scan an unfamiliar module without loading its full body.

2. **Trace who calls the symbol you plan to change.**

   ```text
   tg defs ./src invoice_total --json --detail compact
   tg callers ./src invoice_total --json --detail compact
   ```

   Symbol commands always take the **path first**, then the **symbol name**. Always check `result_incomplete` and `output_limit` before treating an empty caller list as proof that a symbol is unused.

3. **Find related code by plain-English keywords and assemble an edit plan.**

   ```text
   tg find --hybrid 'invoice tax total' ./src --json
   tg prepare ./src 'add tax validation to invoice_total' --json
   ```

   `tg find --hybrid` combines local SQLite full-text search with adaptive Reciprocal Rank Fusion (RRF) — no API key or GPU required. `tg prepare` returns a suggested `primary_target`, `next_action`, caller context, and candidate test commands to review before editing.

4. **Review your diff impact before committing.**

   In a Git repository, after making edits, run:

   ```text
   tg diff-impact . --json
   ```

   `tg diff-impact` inspects your working-tree diff, maps modified symbols to impacted callers and tests, and emits a **change slice** (the minimal set of files and symbols linked to the diff) with a SHA-256 `slice_digest` that CI gates can verify with `tg review-bundle verify --require-change-slice`.

## Connect an AI assistant (MCP)

If you use an editor or coding assistant that speaks **MCP** (Model Context Protocol), start the local MCP server with:

```text
tg mcp
```

- By default, `tg mcp` exposes **15 compact tools** (10 task-shaped meta-tools such as `tg_explore`, `tg_navigate`, `tg_context`, `tg_impact`, `tg_query`, `tg_rewrite`, and `tg_audit`, plus always-on helpers `tg_mcp_capabilities`, `tg_batch`, `tg_classify_logs`, and `tg_discover_tools`). This keeps the tool catalog small (`< 12 KB`) so assistants spend fewer tokens choosing tools.
- Set `TG_MCP_LEGACY_TOOLS=1` only if an older client expects all 61 individual legacy tool names.
- Use `tg_batch` to chain multiple read-only queries in one call (passing outputs forward with `$prev.<field>` or `$steps[i].<field>` and setting `return_final_only=True` to return just the final answer).
- On Windows, point stdio MCP clients directly at the native `tg.exe` executable rather than `tg.ps1`. See [automation recipes](harness_cookbook.md) and the [harness API](harness_api.md) for copyable MCP workflows.

## Where `tg` stores local files

As you run commands, `tg` creates two separate directories at your project root:

- **`.tg_cache/` (safe to rebuild):** Holds the unified manifest (`manifest_v1.json`), trigram search indexes (`index_v1/*.tg_index`), AST cache (`ast/`), symbol cache (`symbols_v1/`), and pre-change test graph (`graph_v1/tdad_tests.sqlite3`). `tg` automatically creates `.tg_cache/.gitignore` so clean Git repos stay untracked.
- **`.tensor-grep/` (handle with care):** Stores reusable sessions (`.tensor-grep/sessions/`) and rollback checkpoints (`.tensor-grep/checkpoints/`). Never delete `.tensor-grep/checkpoints/` when clearing search caches.

See [cache management](runbooks/cache-management.md) before deleting any stored state.

## Reuse a session

A **session** stores a snapshot of project context so related requests can reuse it. A session is local state, not a cloud account. Open one from the practice project's root:

```text
tg session open . --json
```

Inspect the returned `root` before continuing. Session storage anchors to a detected
project root, which may be an ancestor containing `.git` or a project manifest.
If that is not the project you intended, stop and move the practice folder outside
that project before opening another session.

Copy the session ID from the output and replace `SESSION_ID` in the next command:

```text
tg session context SESSION_ID . 'calculate an invoice total' --json
tg session refresh SESSION_ID . --json
```

Do not type `SESSION_ID` literally. Refresh asks the session to update its view of project files. You do not need to start a daemon for these examples. See [sessions and the daemon](session_daemon_protocol.md) for lifecycle and storage details.

## Save a checkpoint before an edit

A **checkpoint** is a saved copy used to restore a specific file. It is rollback material, so treat it differently from a disposable cache. Create a checkpoint for only the sample source file:

```text
tg checkpoint create ./src/invoice.py --json
tg checkpoint list ./src/invoice.py --json
```

On Windows, checkpoint creation requires Windows 11 24H2 or Windows Server 2025 with `CreateDirectory2W`. On POSIX, it requires Python support for descriptor-relative file operations, `O_NOFOLLOW`, and `O_DIRECTORY`; unsupported runtimes report an error before writing checkpoint state.

Before changing the file, inspect the create result. Record its `checkpoint_id`, confirm its scope is the sample file, and read the `undo_command` or `undo_argv` it returned. You can also list the checkpoint as shown above. If you are practicing, make a small change to `invoice.py`, then use the exact ID from that creation result:

```text
tg checkpoint undo CHECKPOINT_ID ./src/invoice.py --json
```

Replace `CHECKPOINT_ID` with the returned ID. Undo restores the saved file and may discard edits made after the checkpoint, so confirm the displayed command targets only the intended practice file before running it. The checkpoint ID selects what to restore.

## What to expect from optional features

Core text search works locally and does not require a GPU, API key, or dense model. `tg find`, dense similarity, GPU execution, and the resident AST worker have separate setup and support limits; see [experimental features](EXPERIMENTAL.md). GPU acceleration is opt-in and experimental. `tg calibrate` measures a CPU/GPU crossover for supported builds; it does not build or warm a text index.

If an example fails, first confirm `tg --version`, the current directory, and that the sample files exist. For install channels and platform limits, see [installation](installation.md) and the [support matrix](SUPPORT_MATRIX.md). For caches and saved project state, see [cache management](runbooks/cache-management.md).

## Glossary

These short definitions are repeated above where the terms first appear:

- **AST:** a tree-shaped representation produced by parsing source code.
- **Backend:** the engine that performs a search or analysis request.
- **Change slice:** the minimal set of files, symbols, and impacted tests connected to a Git diff (identified by a SHA-256 `slice_digest`).
- **Context capsule:** a bounded set of task-relevant files and metadata.
- **Daemon:** an optional background process that can serve repeated session requests.
- **Hybrid search:** local retrieval combining keyword full-text matching (SQLite FTS5 / BM25) with Reciprocal Rank Fusion (RRF) and optional dense/1-bit reranking.
- **Index (trigram index):** saved character-group search data under `.tg_cache/index_v1/` (or legacy `.tg_index`) used to narrow candidate files for repeated text searches.
- **JSON:** a text format for structured values such as objects, arrays, strings, and numbers.
- **MCP (Model Context Protocol):** a standard protocol allowing coding assistants to call `tg` search and analysis tools over stdio (`tg mcp`).
- **Parser:** software that reads source text and identifies its code structure.
- **Session:** saved project context used by related requests.
- **Sidecar:** a separate managed program that a front door may invoke for a command.
- **Symbol:** a named code element, such as a function, class, or variable.
