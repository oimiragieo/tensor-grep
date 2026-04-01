---
name: backend-worker
description: Handles Python-side work: sidecar IPC, benchmark scripts, corpus generators, MCP server extensions, GPU benchmarks, documentation, and compatibility tests.
---

# backend-worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use this worker for:
- Edit planning intelligence (`src/tensor_grep/cli/repo_map.py`) — span extraction, plan seed, context ranking, rendering
- Session system (`src/tensor_grep/cli/session_store.py`) — caching, refresh, serve loop
- Audit/trust features (`src/tensor_grep/cli/audit_manifest.py`, `checkpoint_store.py`) — history, diff, policies, bundles
- Security rule packs (`src/tensor_grep/cli/rule_packs.py`) — new packs, suppressions
- CLI commands (`src/tensor_grep/cli/main.py`) — new commands, flag wiring
- MCP server extensions (`src/tensor_grep/cli/mcp_server.py`) — new tools, parity
- Python benchmark scripts (`benchmarks/`)
- Corpus generators (`benchmarks/gen_corpus.py`)
- JSON schema documentation and example artifacts

## Windows-Specific Notes

- **Shell**: PowerShell is the default. Use `$env:PYTHONPATH = '...\src'` syntax.
- **init.sh not executable**: Run `uv pip install -e ".[dev,ast,nlp]"` instead.
- **Cargo path**: `C:\Users\oimir\.cargo\bin\cargo.exe` (may not be on PATH).
- **tg.exe binary**: Pre-built at `benchmarks/tg_rust.exe` or build with `cargo build --release` -> `rust_core/target/release/tg.exe`.
- **rg.exe**: Available via `benchmarks/rg.zip` auto-extract.
- **sg (ast-grep)**: May need installation via `cargo install ast-grep` if not on PATH.

## Work Procedure

1. **Read feature description and preconditions** carefully. Understand what assertions this feature fulfills.

2. **Test-Driven Development FIRST**: Write a failing Python test (in `tests/`) that exposes the specific behavior or proves the new contract. Run it and verify it fails. Only then implement.

3. Implement the Python logic. Follow:
   - Sidecar protocol: JSON over stdin/stdout, fields: `{command, args, payload}` -> `{status, result, error}`.
   - MCP tools: Use FastMCP pattern from existing tools in `mcp_server.py`.
   - Benchmark scripts: Follow existing patterns (e.g., `run_ast_benchmarks.py`). Always include `suite`, `generated_at_epoch_s`, and `environment` in JSON output.
   - Corpus generators: Ensure generated files are syntactically valid for their language. Preserve backward compatibility of Python corpus.

4. **For benchmark scripts**: Ensure the script:
   - Accepts `--output <path>` for JSON artifact output
   - Produces valid JSON with `suite`, `generated_at_epoch_s`, and `environment` fields
   - Uses hyperfine or Measure-Command for timing (not Python time.time)
   - Handles missing tools gracefully (e.g., `sg` not installed)

5. **For MCP server changes**: Ensure:
   - All tool responses include `routing_backend` and `routing_reason`
   - Invalid input returns structured error messages, not tracebacks
   - Existing tools still work after adding new ones
   - Add tests in `tests/unit/test_mcp_server.py`

6. **For repo_map.py changes** (edit planning, rendering, context):
   - The file is 91K+ — read only the relevant sections, not the whole file
   - Key functions: `build_context_render_from_map` (~line 1700), `build_symbol_blast_radius_render` (~line 2400), `_render_context_parts` (~line 1550), `_render_source_block` (~line 1400), `_validation_commands_for_tests` (~line 1660)
   - Edit plan seed generated at two sites (~line 1805 and ~line 2490) — update BOTH
   - All new JSON fields must be additive (never remove existing fields)
   - Symbol extraction functions: `_python_symbol_definitions` (~line 600), `_js_symbol_definitions` (~line 700), `_ts_symbol_definitions` (~line 730), `_rust_symbol_definitions` (~line 760)

7. **For session_store.py changes**:
   - Key functions: `_stale_reason` (staleness detection), `refresh_session`, `serve_session_stream`
   - Session JSON lives at `<root>/.tensor-grep/sessions/<session_id>.json`
   - JSONL serve protocol: each request has `command` field, response is one JSON line

8. **For audit/trust/ruleset changes**:
   - Follow existing patterns in `audit_manifest.py` (HMAC signing, chain verification)
   - Rule packs use ast-grep AST pattern syntax in `_RULE_PACKS` dict
   - All new CLI commands must have MCP tool parity
   - Policy engine should use subprocess for running lint/test commands with timeout

7. Run local Python gates:
   ```powershell
   uv run ruff check .
   uv run mypy src/tensor_grep
   $env:PYTHONPATH = 'src'; uv run pytest -q
   ```

8. If touching benchmark scripts, run the script and validate its output:
   ```powershell
   python benchmarks/<script>.py --output artifacts/<output>.json
   python -c "import json; json.load(open('artifacts/<output>.json'))"
   ```

## Example Handoff

```json
{
  "salientSummary": "Created benchmarks/run_ast_multilang_benchmarks.py measuring tg vs sg across Python/JS/TS/Rust corpora. Extended gen_corpus.py with --lang javascript/typescript/rust generators. JSON artifact shows Python ratio 1.42x, JS 1.38x, TS 1.45x, Rust 1.51x — all within 3.0 threshold. uv run pytest -q: 515 passed. ruff/mypy clean.",
  "whatWasImplemented": "Added JavaScript, TypeScript, and Rust corpus generators to gen_corpus.py using realistic function/class patterns per language. Created run_ast_multilang_benchmarks.py following existing run_ast_benchmarks.py patterns. Script generates per-language corpora, runs hyperfine, produces JSON with per-language timing rows.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "python benchmarks/gen_corpus.py --kind ast-bench --lang javascript --out /tmp/js_corpus --files 100", "exitCode": 0, "observation": "100 .js files generated" },
      { "command": "python benchmarks/run_ast_multilang_benchmarks.py --output artifacts/bench_ast_multilang.json", "exitCode": 0, "observation": "4 language rows, all ratios < 3.0" },
      { "command": "uv run pytest -q", "exitCode": 0, "observation": "515 passed, 14 skipped" },
      { "command": "uv run ruff check .", "exitCode": 0, "observation": "clean" }
    ],
    "interactiveChecks": [
      { "action": "Ran tg run --lang javascript 'function $F($$$ARGS) { return $EXPR; }' /tmp/js_corpus", "observed": "Found 100 matches across 100 files" }
    ]
  },
  "tests": {
    "added": [
      { "file": "tests/unit/test_gen_corpus.py", "cases": [
        { "name": "test_js_corpus_generates_valid_files", "verifies": "JS files parse without error" },
        { "name": "test_ts_corpus_generates_valid_files", "verifies": "TS files parse without error" },
        { "name": "test_rust_corpus_generates_valid_files", "verifies": "Rust files parse without error" },
        { "name": "test_python_backward_compat", "verifies": "Default Python generation unchanged" }
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Benchmark degraded and you cannot determine if it's Python-layer or Rust-layer
- MCP protocol has a design conflict with the new tools
- Python test failures caused by Rust-side behavior changes
- sg (ast-grep) not available for multi-language benchmarks
- GPU benchmarks need CUDA setup changes beyond the feature scope
