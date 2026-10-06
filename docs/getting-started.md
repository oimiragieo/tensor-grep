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

The first command prints the installed version. The second lists commands available from your installed front door. Python package installs and managed native installs can expose different implementation paths; [architecture](architecture.md) explains that distinction.

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
| Gather files relevant to a task | `tg context ./src 'calculate an invoice total' --max-tokens 1000 --json` | A bounded context result based on local project data |
| Prepare an edit investigation | `tg prepare ./src 'add invoice tax field' --json` | Suggested target and supporting context for review, not approval to edit |

**JSON** is a text format for structured data. The `--json` options make results easier for programs to read; a person can still inspect them. A **context capsule** is a bounded package of selected files and metadata for a task. Limits such as `--max-tokens` constrain output size, not correctness. Caller and impact reports may be partial when a scan reaches its configured bounds; inspect their completeness fields before treating missing results as proof.

## Reuse a session

A **session** stores a snapshot of project context so related requests can reuse it. A session is local state, not a cloud account. Open one from the practice project's root:

```text
tg session open . --json
```

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

Before changing the file, inspect the create result. Record its `checkpoint_id`, confirm its scope is the sample file, and read the `undo_command` or `undo_argv` it returned. You can also list the checkpoint as shown above. If you are practicing, make a small change to `invoice.py`, then use the exact ID from that creation result:

```text
tg checkpoint undo CHECKPOINT_ID ./src/invoice.py --json
```

Replace `CHECKPOINT_ID` with the returned ID. Undo restores the saved file and may discard edits made after the checkpoint, so confirm the displayed command targets only the intended practice file before running it. The current source checkout accepts optional `--label` metadata, but published PyPI `1.123.23` does not; omit labels when following this guide with that package. Labels are descriptive only; the checkpoint ID selects what to restore.

## What to expect from optional features

Core text search works locally and does not require a GPU, API key, or dense model. `tg find`, dense similarity, GPU execution, and the resident AST worker have separate setup and support limits; see [experimental features](EXPERIMENTAL.md). GPU acceleration is opt-in and experimental. `tg calibrate` measures a CPU/GPU crossover for supported builds; it does not build or warm a text index.

If an example fails, first confirm `tg --version`, the current directory, and that the sample files exist. For install channels and platform limits, see [installation](installation.md) and the [support matrix](SUPPORT_MATRIX.md). For caches and saved project state, see [cache management](runbooks/cache-management.md).

## Glossary

These short definitions are repeated above where the terms first appear:

- **AST:** a tree-shaped representation produced by parsing source code.
- **Backend:** the engine that performs a search or analysis request.
- **Context capsule:** a bounded set of task-relevant files and metadata.
- **Daemon:** an optional background process that can serve repeated session requests.
- **Index:** saved search data used to narrow candidate files for some repeated text searches.
- **JSON:** a text format for structured values such as objects, arrays, strings, and numbers.
- **Parser:** software that reads source text and identifies its code structure.
- **Session:** saved project context used by related requests.
- **Sidecar:** a separate managed program that a front door may invoke for a command.
- **Symbol:** a named code element, such as a function, class, or variable.
