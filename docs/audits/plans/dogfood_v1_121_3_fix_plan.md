# Implementation Plan: Fix & Modernization for tensor-grep (v1.121.3) Dogfood Audit (Revision 11 - Final)

## 1. Problem Statement & Objectives
Following the Comprehensive End-to-End Dogfood Audit Report (v1.121.3) and Thinktank Council Reviews (Rounds 1–10), this finalized plan incorporates all council feedback: exact `deadline_monotonic` parameter usage, explicit deferred A48 handle-anchoring boundary, preservation of `_detect_checkpoint_scope` for file-scoped checkpoints, scoped destination confinement rejecting aliases/symlinks escaping the selection across restore/deletion/rollback, enforceable SQLite connection limits, safe-function allowlisting, duplicate column rejection, bounded lookahead truncation distinguishing exact limit matches from truncation, output truncation dual-flag parity (`truncated: true` and `result_incomplete: true`), reliable SQLite deadline classification using a handler flag and `sqlite_errorcode == SQLITE_INTERRUPT` mapping to exit 2 with deadline diagnostics, incremental row-by-row byte accounting under 5MB budget, front-door registration completeness, pre-flight checkpoint path containment, scoped undo file preservation, and package version desync detection.

---

## 2. Invariant Contracts & Front-Door Registration Sites
Any new top-level command (`repair-env`, `sql`) MUST be enrolled across all four canonical front-door sites:
1. `src/tensor_grep/cli/commands.py`: Add command name to the `KNOWN_COMMANDS` set.
2. `src/tensor_grep/cli/main.py`: Implement `@app.command(name=...)` with type annotations, docstring, and clean error handling.
3. `rust_core/src/main.rs`:
   - Add explicit clap subcommand enum variants: `Commands::RepairEnv` and `Commands::Sql` to the `Commands` enum (around line 699).
   - In `run_command_cli` (around line 7272): Add dispatch arms that forward them to `handle_python_passthrough("repair-env", ...)` and `handle_python_passthrough("sql", ...)`.
4. `tests/e2e/test_routing_parity.py`: Add command names to `PUBLIC_TOP_LEVEL_COMMANDS` set (line 46) so cross-language front-door parity tests pass cleanly.

---

## 3. Detailed Scope of Changes

### Task 1: Clean Up `tg route-test` Deprecation & Stream Isolation (Defect 1)
- **Warning Propagation Contract**:
  - Update `_resolve_path_and_query` (`src/tensor_grep/cli/main.py:7351`) to return a 3-tuple:
    `tuple[str, str, str | None]` (`resolved_path, resolved_query, deprecation_warning`).
  - When `--query` is passed: return the deprecation warning string instead of writing directly to stderr.
  - In `route_test` (`src/tensor_grep/cli/main.py:6475`):
    - When `json_output=True`: Append `deprecation_warning` into `payload["warnings"]`. Emit zero bytes to `stderr`. This guarantees 100% clean stdout JSON parseability in automated workflows.
    - When `json_output=False`: Print `deprecation_warning` to `stderr` via `typer.echo(..., err=True)` before text output.
  - In other callers of `_resolve_path_and_query`: Print to stderr as before if a warning is returned.
  - Preserve exit code contracts: exit 0 on full agreement, exit 2 on truncation/deadline trip.
  - Update CLI callers, docs, and tests to positional `tg route-test <PATH> <QUERY>`.

### Task 2: Python Package Version Desync Detection & `tg repair-env` (Defect 2)
- **Doctor Diagnostic Enhancement**:
  - In `src/tensor_grep/cli/doctor_report.py` and `doctor_payload.py`:
    - Read `source_version = _read_project_version_fallback()` (canonical from `runtime_paths.py:82`).
    - Read `installed_version = _self._doctor_installed_version()` (`importlib.metadata.version('tensor-grep')`).
    - Inspect editable install provenance: inspect `direct_url.json` from `importlib.metadata.Distribution.from_name("tensor-grep")`. Confirm `"dir_info": {"editable": True}` and match `url` against repo root.
    - If `source_version != "0.0.0"` and `source_version != installed_version`:
      - Set `python_package_version_status: "stale_editable"`.
      - In `_doctor_rust_binary_remediation` (`doctor_report.py:499`): If `rust_binary_version` matches `source_version`, explicitly state that the native binary is up to date and that the `.venv` dist-info metadata is lagging behind `pyproject.toml`.
      - Prescribe exact remediation: `Run 'tg repair-env' or 'uv pip install -e . --no-deps' to re-sync editable package metadata.`
- **New Command `tg repair-env`**:
  - Enrolled across all 4 sites (`commands.py`, `main.py`, `rust_core/src/main.rs`, `test_routing_parity.py:46`).
  - Implementation in `src/tensor_grep/cli/main.py`:
    - Verify editable distribution provenance: verify that the running environment has `tensor-grep` installed in editable mode pointing to a verified source root containing `pyproject.toml`. Reject non-editable wheel installs and unrelated directories fail-closed before executing any subprocess.
    - Target current python interpreter: `sys.executable`.
    - Execute `[sys.executable, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"]` (or use `uv pip` targeting `--python sys.executable` if uv is present).
    - Capture output; emit structured JSON under `--json` with `{status, previous_version, new_version, duration_seconds}`.
    - Exit 0 on success, exit 1 on failure.

### Task 3: `tg run` AST Match Precision & Pattern Documentation (Defect 3)
- **Usability & Syntax Examples**:
  - In `rust_core/src/main.rs` (`RunArgs` clap doc) and `src/tensor_grep/cli/main.py` (`run` Typer docstring):
    - Document ast-grep pattern constraints for Python and TypeScript multiline functions:
      - Python multiline function: `def $NAME($$$ARGS):\n    $$$BODY` (not single-line `def $NAME($$$ARGS): $$$BODY`).
      - TypeScript multiline function: `function $NAME($$$ARGS) {\n  $$$BODY\n}`.
      - Contrast statement-level matching vs block-level matching.
      - Document `--selector function_definition` / `method_definition` as the robust syntax for matching any function regardless of formatting.

### Task 4: Dense Semantic Search Fallback & Pure-Rust Embeddings Architecture (Defect 4 & Imp 1)
- **Clear Fallback Messaging**:
  - In `src/tensor_grep/cli/main.py` and `src/tensor_grep/core/retrieval_dense.py` (lines 74-81: `rank_fallback_reason` and `install-dense` hint sites):
    - Ensure that when `model2vec` is not installed, the notice clearly states: `BM25 lexical ranking active (100% functional). For dense semantic vector reranking, run tg install-dense.`
  - Document pure-Rust ONNX embedding architecture (via `tract` or `ort` in `rust_core`) in `docs/architecture/native_embeddings.md`.

### Task 5: `tg calibrate` Guidance on Non-CUDA Builds (Defect 5)
- **Preserve Exit-Code Contract (Exit 2) & Document CUDA Requirements**:
  - Preserve existing exit code contract: On CPU-only builds without CUDA, `tg calibrate` returns exit code 2 with `"calibration_status": "skipped_no_cuda_build"` (per backend-unavailable convention).
  - Update `CalibrateArgs` help in `rust_core/src/main.rs` and `src/tensor_grep/cli/main.py` to clarify that GPU calibration requires NVIDIA hardware and CUDA-enabled binary builds.

### Task 6: Interactive Symbol Disambiguation for `defs` and `source` (Improvement 2)
- **Bounded Levenshtein / Difflib Suggestions**:
  - In `src/tensor_grep/cli/main.py` in `_emit_symbol_command_result` (`main.py:7180`):
    - Only trigger suggestion search when `not_found == True`.
    - Candidate source: Extract candidate symbol names from the scanned repository map / AST symbol inventory before compaction, or from the target file's AST symbols. Do NOT execute a second unbounded repository scan.
    - Compute `difflib.get_close_matches(symbol, candidates, n=5, cutoff=0.6)`.
    - Sort matches deterministically (by similarity score descending, then alphabetical).
    - Cap suggestion strings to max 64 chars.
    - Populate `payload["suggestions"] = matches`.
    - In text mode: If matches exist, emit `Did you mean: {', '.join(matches)}?`.
    - **Crucial Invariant**: Preserve exit code 1 for symbol not found, and exit 2 for scan truncation / incomplete results. Suggestions NEVER alter exit codes.

### Task 7: Multi-File Checkpoint Granularity (`--paths`) with Confinement & Undo Safety (Improvement 3)
- **Scope Detection & Path Confinement**:
  - In `src/tensor_grep/cli/checkpoint_store.py`:
    - Retain `scope = _detect_checkpoint_scope(Path(path))` (`checkpoint_store.py:292–299`) to preserve 100% regression parity for existing file-scoped (`scope_kind == "file"`) and whole-tree (`scope_kind == "tree"`) checkpoints when `paths is None`.
    - When `paths` is passed:
      - If `scope.scope_kind == "file"`: Fail closed with `ValueError("Cannot specify both a single-file path and --paths")`.
      - Root anchor: `root = scope.root` (repository or directory root).
      - For each path in `paths`:
        - Normalize and reject paths outside `root`: verify `candidate.resolve().is_relative_to(root.resolve())` fail-closed.
        - Pre-flight directory traversal checks (`_resolve_within_root` / `_resolve_parent_within_root`) covering pre-planted/pre-existing escapes.
        - Explicit deferred boundary: opened-parent-handle anchoring for TOCTOU parent swaps (`CHECKPOINT-A48-HANDLES`) remains explicitly deferred per line 790 with its recorded owner and reopen trigger.
        - Filter snapshot walk to files within the resolved candidate paths.
      - Record `scoped_paths` in the checkpoint's `metadata.json` manifest.
- **Scoped Undo Protection (Prevent Data Loss & Scope-Escaping Aliases)**:
  - In `undo_checkpoint` (`src/tensor_grep/cli/checkpoint_store.py:1207`):
    - Inspect checkpoint manifest for `scoped_paths`.
    - When `scoped_paths` is set:
      - ONLY restore, delete, or clean up files that fall strictly within `scoped_paths`.
      - Filter `current_entries` removal set (`set(scoped_current_entries) - expected_paths`) so files outside `scoped_paths` are NEVER removed or overwritten.
      - Pre-flight destination check against selected scope: verify each target file path resolves within `root` AND that its resolved path resolves within one of the recorded `scoped_paths` (preventing an in-scope symlink or junction from pointing to an unselected file e.g. `unselected/b.txt` and overwriting or deleting it during restore, deletion, or rollback). Reject leaf or parent aliases escaping the recorded selection fail-closed.
    - File-scoped (`scope_kind == "file"`) and whole-tree undo paths remain 100% preserved.
  - In `src/tensor_grep/cli/main.py`:
    - Add `--paths` option (`list[str] | None`) to `@checkpoint_app.command("create")`.

### Task 8: Structured AST Querying (`tg sql`) with Read-Only Security Sandbox & Enforceable Resource Bounds (Improvement 4)
- **Front-Door Registration**:
  - Register `sql` across all 4 sites (`commands.py`, `main.py`, `rust_core/src/main.rs`, `tests/e2e/test_routing_parity.py:46`).
- **Bounded Symbol Extraction Seam**:
  - Use `build_repo_map(path, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic)` in `src/tensor_grep/cli/repo_map.py:4242` where `deadline_monotonic = time.monotonic() + deadline_seconds` (default `max_repo_files=1000`, `deadline_seconds=30.0`).
  - If the repo scan was partial/truncated, carry `result_incomplete: true` / `partial: true` into the final SQL payload and exit 2 (per repository incomplete convention), even if the SQL query succeeded on partial data.
- **Database Schema**:
  - In-memory SQLite (`:memory:`):
    ```sql
    CREATE TABLE symbols (
        file TEXT NOT NULL,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        language TEXT NOT NULL,
        signature TEXT
    );
    CREATE INDEX idx_symbols_file ON symbols(file);
    CREATE INDEX idx_symbols_kind ON symbols(kind);
    CREATE INDEX idx_symbols_name ON symbols(symbol);
    ```
  - Populate table with extracted symbol records.
- **Enforceable Connection Limits & Security Authorizer**:
  - Set SQLite connection limits (`conn.setlimit`):
    - `SQLITE_LIMIT_LENGTH = 1_000_000` (1MB max blob/string length, preventing memory bombs).
    - `SQLITE_LIMIT_COLUMN = 100` (max projection columns).
    - `SQLITE_LIMIT_SQL_LENGTH = 10_000` (max SQL query text length).
  - Register `conn.set_authorizer(read_only_authorizer)` strictly AFTER table creation and row insertion:
    - Allow: `SQLITE_SELECT`, `SQLITE_READ`.
    - For `SQLITE_FUNCTION`: Enforce a safe-function allowlist (`count`, `upper`, `lower`, `substr`, `substring`, `length`, `min`, `max`, `sum`, `avg`, `trim`, `ltrim`, `rtrim`, `coalesce`, `typeof`, `instr`, `like`, `glob`, `round`, `abs`, `nullif`, `hex`, `quote`, `unicode`, `char`). Strictly DENY memory/process hazard functions including `randomblob`, `zeroblob`, `load_extension`, and any unrecognized function name.
    - Deny: `SQLITE_ATTACH`, `SQLITE_DETACH`, `SQLITE_INSERT`, `SQLITE_UPDATE`, `SQLITE_DELETE`, `SQLITE_CREATE_*`, `SQLITE_DROP_*`, `SQLITE_ALTER_*`, `SQLITE_PRAGMA`.
- **Query Execution, Lookahead Truncation, Duplicate Column Rejection & Cooperative Deadline**:
  - Reject multiple statements (enforce single SQL statement; disallow `;` chaining).
  - Inspect `cursor.description` for duplicate column names: if duplicates exist (e.g. `SELECT symbol AS x, kind AS x`), reject fail-closed with `Query contains duplicate column name(s): 'x'. Use distinct column aliases.` (exit 1).
  - Cooperative VM opcode deadline: maintain a mutable `deadline_tripped = {"hit": False}` flag; register `conn.set_progress_handler(progress_handler, 1000)` where `progress_handler` checks `time.monotonic() >= deadline` (2 seconds max wall clock) and, if exceeded, sets `deadline_tripped["hit"] = True` and returns non-zero to abort execution.
  - Incremental row-by-row byte accounting & bounded lookahead:
    - Attempt to fetch up to `limit + 1` rows incrementally via `cursor.fetchone()`.
    - If `limit + 1` rows are available, retain only `limit` rows and mark `truncated = True` AND `result_incomplete = True`. If <= `limit` rows are returned, mark `truncated = False` and `result_incomplete = False` (unless scan was partial).
    - Before appending each row, compute and track accumulated payload bytes (row dict plus envelope and formatting overhead) against a strict 5MB aggregate budget.
    - If the 5MB budget is exceeded during iteration, stop immediately, retain rows fetched before the threshold, and mark `truncated = True` AND `result_incomplete = True`.
- **Output & Exit Code Invariants**:
  - Plain text: formatted Markdown/ASCII table.
  - `--json`: machine-readable array of row dicts inside standard JSON envelope.
  - Query deadline interruption: catch `sqlite3.Error` across both execution (`cursor.execute`) and fetching (`cursor.fetchone`), classifying a deadline interruption if `deadline_tripped["hit"]` is True OR `getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT`. Map this explicitly to exit 2 with `result_incomplete: true`, `partial: true`, `deadline_exceeded: true`, and `error: "query_deadline_exceeded"`, distinguishing timeout from syntax/authorizer errors.
  - Exit code contract:
    - Exit 0: Query completed fully within limits without scan or output truncation (`truncated: false`, `result_incomplete: false`).
    - Exit 1: SQL syntax error, authorizer denial, duplicate column names, or invalid query (`result_incomplete: false`).
    - Exit 2: Scan truncation (`result_incomplete: true`), output truncation (`truncated: true` AND `result_incomplete: true` from row limit lookahead or 5MB byte budget exhaustion), OR query execution deadline interruption (`deadline_exceeded: true`, `result_incomplete: true`), adhering strictly to tensor-grep's invariant that partial/incomplete results exit 2.

---

## 4. Verification & Testing Strategy
1. **Stream Isolation Test**:
   - `tg route-test <PATH> --query "..." --json` produces zero bytes on stderr; warning in `payload["warnings"]`.
2. **Positional Syntax Test**:
   - `tg route-test <PATH> "..." --json` produces zero warnings and identical target resolution.
3. **Doctor Version Desync Test**:
   - Verify `python_package_version_status: "stale_editable"` when `pyproject.toml` > installed version.
4. **Front-door Parity Test**:
   - Run `pytest tests/e2e/test_routing_parity.py` proving `repair-env` and `sql` match between Python and Rust.
5. **Symbol Disambiguation Test**:
   - Test misspelled symbol suggestions; verify exit code 1 is preserved.
6. **Checkpoint Path Confinement & Scoped Undo Test**:
   - Verify `tg checkpoint create --paths ../outside` fails closed.
   - Verify `tg checkpoint undo` on a scoped checkpoint does NOT delete files outside scoped paths.
   - Verify `tg checkpoint undo` on a scoped checkpoint rejects destination symlinks escaping the recorded scope (preventing unselected file overwrite).
7. **SQL Read-Only Sandbox & Resource Bounds Test**:
   - Verify connection limits (rejection/truncation of strings/blobs > 1MB, columns > 100, SQL > 10KB).
   - Verify authorizer blocks dangerous functions (`randomblob`, `zeroblob`, `load_extension`) and write statements (`ATTACH`, `INSERT`).
   - Verify safe functions succeed (`SELECT count(*), upper(symbol) FROM symbols WHERE kind='function'`).
   - Verify duplicate projection columns are rejected fail-closed with exit 1.
   - Verify lookahead truncation and exit status: fewer-than-limit (exit 0, truncated=false, result_incomplete=false), exactly-limit (exit 0, truncated=false, result_incomplete=false), limit-plus-one (exit 2, truncated=true, result_incomplete=true), and byte-budget exhaustion (exit 2, truncated=true, result_incomplete=true).
   - Verify cooperative query deadline interruption aborts long queries via real progress-handler interruption both before the first row and during fetching, asserting all four fields: `result_incomplete: true`, `partial: true`, `deadline_exceeded: true`, and `error: "query_deadline_exceeded"` with exit 2.
   - Verify scan deadline / truncation triggers exit 2.
8. **Codex Subagent Audit**:
   - Dispatch OpenAI Codex (`gpt-6-sol`) to audit the entire diff against this plan.
