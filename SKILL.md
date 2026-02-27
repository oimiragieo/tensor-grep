---
name: tensor-grep
description: Use when searching text in files, codebases, logs, or documents at scale. Use when finding files by pattern, searching massive logs that are too big for CPU regex, extracting specific content from many files, when you need semantic/AST structural code queries, or when you need NLP classification of threats in server logs. Triggers on "search for", "find occurrences", "look for pattern", "search in files", "classify logs", or "structural code search".
---

# tensor-grep (tg) - GPU-Accelerated Search & AI Parser

## Overview

`tensor-grep` (tg) is a line-oriented search tool that scales regex operations across multi-GPU VRAM arrays via NVIDIA RAPIDS cuDF, providing **3x-10x faster** throughput than standard ripgrep on massive datasets. In addition to lightning-fast text search, it provides AST-based structural code searching and cyBERT NLP log classification.

Because of its hybrid routing architecture, `tensor-grep` acts as a superset orchestrator. For small queries or single files, it automatically wraps the ultra-fast C/Rust binaries (`ripgrep` and `ast-grep`) locally. For exact literal counts, it drops into a native Arrow/Rust zero-copy engine. For massive data or semantic operations, it routes to PyTorch and `cuDF`.

## Core MCP Capabilities Exposed to AI

When using `tensor-grep` via the Model Context Protocol, you have access to three primary tools:

1. **`tg_search`**: The primary regex and text extraction tool. Supports case-insensitivity, fixed string matching, word boundaries, context lines (`-C`), file globs (`-g`), file types (`-t`), and match counting (`-c`).
2. **`tg_ast_search`**: The structural code search tool. Feed it AST patterns like `if ($A) { return $B; }` to locate complex logical bounds across massive monorepos instantly using GNN VRAM tensors.
3. **`tg_classify_logs`**: The cybersecurity and semantic log tool. Pass unstructured server logs through the CyBERT model to identify hidden anomalies, malicious payloads, and severity levels.

## When to Use

**Use tensor-grep when:**
- Searching for text patterns across a massive codebase or multi-GB server logs
- Finding all occurrences of a function, variable, or structural pattern ignoring whitespace (`tg run --ast`)
- Classifying system logs for cybersecurity threats via AI without strict regex rules (`tg classify`)
- Looking for specific content in many files at once with full `ripgrep` drop-in flag compatibility
- Extracting matching lines for analysis rapidly 

**Don't use when:**
- You need the full file content (use Read tool)
- Simple glob pattern matching for filenames only (use Glob tool)
- You need structured data extraction from relational DBs (consider jq, awk)

## Quick Reference

| Task | Command |
|------|---------|
| Basic GPU regex search | `tg "pattern" [path]` |
| Case insensitive | `tg -i "pattern"` |
| Smart case (auto) | `tg -S "pattern"` |
| Whole word only | `tg -w "word"` |
| Fixed string (no regex) | `tg -F "literal.string"` |
| Show context lines | `tg -C 3 "pattern"` (3 before & after) |
| AST Structural Search | `tg run --ast --lang python "def $_($A): return $B"` |
| NLP Threat Classification | `tg classify /var/logs/nginx.log` |
| Force CPU backend | `tg --cpu "pattern"` |

## File Filtering

### By File Type

`tensor-grep` inherits all ripgrep built-in file type definitions. Use `-t` to include, `-T` to exclude:

```bash
# Search only Python files
tg -t py "def main"

# Search only JavaScript and TypeScript
tg -t js -t ts "import"

# Exclude test files
tg -T test "function"

# List all known types
tg --type-list
```

**Common types:** `py`, `js`, `ts`, `rust`, `go`, `java`, `c`, `cpp`, `rb`, `php`, `html`, `css`, `json`, `yaml`, `md`, `txt`, `sh`

### By Glob Pattern

```bash
# Only .tsx files
tg -g "*.tsx" "useState"

# Exclude node_modules (in addition to gitignore)
tg -g "!node_modules/**" "pattern"

# Only files in src directory
tg -g "src/**" "pattern"
```

## Directory Control

```bash
# Limit depth
tg --max-depth 2 "pattern"

# Search hidden files (dotfiles)
tg --hidden "pattern"

# Ignore all ignore files (.gitignore, etc.)
tg --no-ignore "pattern"
```

## Context Options

```bash
# Lines after match
tg -A 5 "pattern"

# Lines before match
tg -B 5 "pattern"

# Lines before and after
tg -C 5 "pattern"
```

## Output Formats

```bash
# Just filenames with matches
tg -l "pattern"

# Count matches per file
tg -c "pattern"

# Only the matched text (not full line)
tg -o "pattern"

# JSON output (for parsing/integrations)
tg --json "pattern"
```

## Specialized AI/GPU Features

### AST / Structural Searching
Avoid brittle regex formatting dependencies by parsing the actual source trees:
```bash
tg run --ast --lang python "if ($A) { return $B; }" ./src
```

### cyBERT NLP Classification
Pass unstructured, messy logs to the HuggingFace transformer engine. It will identify if the lines contain base64 payloads, SQL injections, or anomalies, assigning confidence scores:
```bash
tg classify /var/log/syslog --format json
```

## Combining with Other Tools

```bash
# Search and count by file
tg -c "pattern" | sort -t: -k2 -rn

# Search and open in editor
tg -l "pattern" | xargs code

# Extract unique matches
tg -o "\b[A-Z]{2,}\b" | sort -u
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Matches found |
| 1 | No matches found |
| 2 | Error occurred |
