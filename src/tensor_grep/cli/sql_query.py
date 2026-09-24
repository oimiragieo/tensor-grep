"""`tg sql`: read-only SQL over the AST symbol inventory in an in-memory SQLite sandbox.

Lives outside ``main.py`` (file-size ratchet); registered there as a thin command. The scan
completeness helpers are imported from ``main`` at call time (``main`` imports this module).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

# Mirrors main._DEFAULT_AGENT_REPO_SCAN_LIMIT (asserted equal in the unit tests).
_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000


_SAFE_SQL_FUNCTIONS = {
    "count",
    "upper",
    "lower",
    "substr",
    "substring",
    "length",
    "min",
    "max",
    "sum",
    "avg",
    "trim",
    "ltrim",
    "rtrim",
    "coalesce",
    "ifnull",
    "nullif",
    "typeof",
    "instr",
    "like",
    "glob",
    "round",
    "abs",
    "sign",
    "total",
    "hex",
    "quote",
    "unicode",
    "char",
    "group_concat",
    "string_agg",
    "replace",
    "printf",
    "format",
    "iif",
    "concat",
    "concat_ws",
    "octet_length",
}


def _has_multiple_sql_statements(query: str) -> bool:
    """Return True if query contains multiple SQL statements separated by semicolons."""
    in_single = False
    in_double = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(query)
    while i < n:
        c = query[i]
        next_c = query[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
        elif in_block_comment:
            if c == "*" and next_c == "/":
                in_block_comment = False
                i += 1
        elif in_single:
            if c == "'":
                if next_c == "'":
                    i += 1
                else:
                    in_single = False
        elif in_double:
            if c == '"':
                if next_c == '"':
                    i += 1
                else:
                    in_double = False
        elif in_backtick:
            if c == "`":
                in_backtick = False
        else:
            if c == "-" and next_c == "-":
                in_line_comment = True
                i += 1
            elif c == "/" and next_c == "*":
                in_block_comment = True
                i += 1
            elif c == "'":
                in_single = True
            elif c == '"':
                in_double = True
            elif c == "`":
                in_backtick = True
            elif c == ";":
                rem = query[i + 1 :]
                rem_has_code = False
                r_line = False
                r_block = False
                j = 0
                while j < len(rem):
                    rc = rem[j]
                    r_next = rem[j + 1] if j + 1 < len(rem) else ""
                    if r_line:
                        if rc == "\n":
                            r_line = False
                    elif r_block:
                        if rc == "*" and r_next == "/":
                            r_block = False
                            j += 1
                    else:
                        if rc == "-" and r_next == "-":
                            r_line = True
                            j += 1
                        elif rc == "/" and r_next == "*":
                            r_block = True
                            j += 1
                        elif not rc.isspace() and rc != ";":
                            rem_has_code = True
                            break
                    j += 1
                if rem_has_code:
                    return True
        i += 1
    return False


def _sql_read_only_authorizer(
    action: int,
    param1: str | None,
    param2: str | None,
    db_name: str | None,
    trigger_or_view: str | None,
) -> int:
    import sqlite3

    sqlite_recursive = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite_recursive):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        func_name = (param2 or "").lower()
        if func_name in _SAFE_SQL_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


MAX_SQL_PAYLOAD_BYTES: int = 5 * 1024 * 1024


def _install_query_deadline_handler(
    conn: Any, deadline: float, *, clock: Any = None
) -> dict[str, bool]:
    """Install the query-execution deadline as a SQLite progress handler and return the mutable
    ``deadline_tripped`` flag the caller checks after the query completes or raises.

    Factored into its OWN module-level function (cleanup after Sol's audit) so this module has
    exactly ONE textual declaration of the progress-handler callback -- never two
    ``def progress_handler(): ...`` blocks that a diff-based static-analysis pass over an
    intermediate patch could read as a redeclaration/obscured-declaration.  Exercised by
    ``test_sql_cooperative_deadline_interruption`` (a real recursive CTE against a tight
    ``--deadline``, asserting the exact ``query_deadline_exceeded`` JSON shape and exit 2).

    Sol audit round 5: the caller calls this TWICE on the same connection -- once (discarded
    result) before the imports-reference probe/pass so THAT work stays bounded, and again right
    before the real query executes, to give the query its OWN fresh clock baseline. ``clock``
    (default ``time.monotonic``) is the same test seam `_run_imports_pass` uses, so a test can
    inject one deterministic counter shared across both this function and `_run_imports_pass` to
    prove the reset actually happens, without any real wall-clock sleep.
    """
    import time

    if clock is None:
        clock = time.monotonic

    query_deadline_monotonic = clock() + deadline
    deadline_tripped = {"hit": False}

    def progress_handler() -> int:
        if clock() >= query_deadline_monotonic:
            deadline_tripped["hit"] = True
            return 1
        return 0

    conn.set_progress_handler(progress_handler, 1000)
    return deadline_tripped


def _detect_imports_referenced(conn: Any, query: str) -> bool:
    """Prepare-only pass: does *query* actually touch the ``imports`` table?

    Perf finding (CEO round 3 on PR #1175): the imports pass added a +90% latency tax to EVERY
    `tg sql` call, including a bare ``SELECT * FROM symbols`` that never looks at `imports`. A
    substring/regex check on the query text would be a parser the real SQL grammar can always
    outrun (a CTE, a subquery, a comment containing the word "imports", a case difference). This
    instead asks SQLite itself: compile *query* with ``EXPLAIN`` (compiles the real statement,
    including every subquery/CTE/alias it references, but does not execute the resulting VDBE
    program) against a connection that already has the `imports` SCHEMA (empty) in place, with an
    authorizer that records every ``SQLITE_READ`` on table ``"imports"``. The authorizer callback
    receives SQLite's own resolved table name -- never an alias -- so `FROM imports AS i`, a CTE
    that selects from `imports`, and a sub-SELECT all resolve to the same `param1 == "imports"`
    check. Deliberately permissive (always returns ``SQLITE_OK``): this is pure detection, not
    enforcement -- the real run's `_sql_read_only_authorizer` still gates execution.

    A query that fails to even compile (bad syntax, or some `EXPLAIN`-incompatible shape) is
    reported as referencing `imports` -- fail TOWARD building the table, never toward silently
    skipping a real dependency; the real run below will raise the identical error either way.
    """
    import sqlite3

    explain_query = query if query.strip().upper().startswith("EXPLAIN") else f"EXPLAIN {query}"
    referenced = {"hit": False}

    def _detect_authorizer(
        action: int,
        param1: str | None,
        param2: str | None,
        db_name: str | None,
        trigger_or_view: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_READ and param1 == "imports":
            referenced["hit"] = True
        return sqlite3.SQLITE_OK

    conn.set_authorizer(_detect_authorizer)
    try:
        conn.execute(explain_query)
    except sqlite3.Error:
        referenced["hit"] = True
    finally:
        conn.set_authorizer(None)
    return referenced["hit"]


def _run_imports_pass(
    repo_map: dict[str, Any],
    deadline_monotonic: float,
    *,
    lang_registry_module: Any,
    imports_with_lines_for_path: Any,
    infer_project_root: Any,
    max_parse_bytes: Any,
    resolve_raw_import_entry: Any,
    supported_languages: frozenset[str],
    norm_path: Any,
    clock: Any = None,
) -> tuple[list[tuple[str, str, int, str | None]], bool, bool]:
    """The actual `imports` extraction+resolution pass -- ONLY called when
    `_detect_imports_referenced` says the query needs it (perf finding, CEO round 3 on #1175).

    Defined as a MODULE-LEVEL function in `sql_query.py` (not `repo_map.py`) precisely so a test
    can spy on ITS entry point directly (`monkeypatch.setattr(sql_query, "_run_imports_pass",
    spy)`) without touching a `repo_map`/`main`/`mcp_server` symbol -- patching one of those three
    files' own attributes is what `test_bare_call_ratchet.py`'s retired-to-zero pins guard
    against (a prior round of this same PR re-tripped that ratchet exactly this way).

    Returns ``(import_records, imports_deadline_hit, imports_unsupported_files_hit)``. Line-aware
    raw extraction comes from `repo_map._imports_with_lines_for_path` (10 supported languages);
    resolution to a target file comes from `repo_map._resolve_raw_import_entry`, which reports
    honestly-unresolved (NULL) rather than guessing. Sol audit round 2, finding 4: resolution
    itself is memoized per (importing-dir, repo-root, language, module, level, dynamic flags) --
    the same shape recurs constantly across a repo (every file importing `os`/`json`/a shared
    internal package).

    ``clock`` (Sol audit round 3, tests-only finding): a zero-arg callable returning a float,
    defaulting to `time.monotonic`. Injecting the clock HERE -- scoped to only this pass -- lets a
    test drive a deterministic counter (expire after exactly N resolve calls) without also
    slowing the repo-map SCAN this function is called after; a global `time.monotonic` patch
    (the prior round's approach) taxed both and made "did the cutoff land mid-file" a function of
    real wall-clock timing, not of the exact call the test meant to control.
    """
    import time

    if clock is None:
        clock = time.monotonic

    resolve_cache: dict[tuple[str, str, str, str, int, bool, bool], dict[str, Any]] = {}
    imports_deadline_hit = False
    imports_unsupported_files_hit = False
    import_records: list[tuple[str, str, int, str | None]] = []
    for file_str in repo_map.get("files", []):
        if clock() >= deadline_monotonic:
            imports_deadline_hit = True
            break
        file_path = Path(str(file_str))
        spec = lang_registry_module.spec_for_path(file_path)
        if spec is None:
            # Honestly-unsupported (e.g. Kotlin, or any non-registered-language file) -- never
            # silently read as "this file has zero imports" (mirrors `build_file_imports`).
            imports_unsupported_files_hit = True
            continue
        if spec.language_id not in supported_languages:
            imports_unsupported_files_hit = True
            continue
        try:
            file_size = file_path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > max_parse_bytes():
            imports_unsupported_files_hit = True
            continue
        repo_root = infer_project_root(file_path)
        raw_entries = imports_with_lines_for_path(file_path)
        file_deadline_hit = False
        for raw_entry in raw_entries:
            if clock() >= deadline_monotonic:
                imports_deadline_hit = True
                file_deadline_hit = True
                break
            cache_key = (
                str(file_path.parent),
                str(repo_root),
                str(spec.language_id),
                str(raw_entry.get("module", "")),
                int(raw_entry.get("level", 0) or 0),
                bool(raw_entry.get("dynamic", False)),
                bool(raw_entry.get("dynamic_unresolved", False)),
            )
            cached = resolve_cache.get(cache_key)
            if cached is not None:
                resolved_entry = cached
            else:
                resolved_entry = resolve_raw_import_entry(
                    file_path, raw_entry, repo_root, str(spec.language_id)
                )
                resolve_cache[cache_key] = resolved_entry
            resolved_file = resolved_entry.get("resolved")
            import_records.append((
                norm_path(str(file_path)),
                str(resolved_entry.get("module", "")),
                int(raw_entry.get("line", 0) or 0),
                norm_path(str(resolved_file)) if resolved_file else None,
            ))
        if file_deadline_hit:
            break
    return import_records, imports_deadline_hit, imports_unsupported_files_hit


def sql_command(
    arg1: str = typer.Argument(..., help="Path to search, or SQL query string."),
    arg2: str | None = typer.Argument(None, help="SQL query string (if path was first argument)."),
    limit: int = typer.Option(100, "--limit", "-n", min=1, help="Maximum rows to return."),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repository files to scan for symbols.",
    ),
    deadline: float = typer.Option(
        2.0,
        "--deadline",
        min=0.1,
        help="Query execution deadline in seconds (progress handler abort).",
    ),
    scan_deadline: float = typer.Option(
        30.0,
        "--scan-deadline",
        min=1.0,
        help="Repository scan deadline in seconds.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Query AST symbols and imports across a codebase using structured SQL in a read-only sandbox.

    Two tables: ``symbols(file, symbol, kind, line, end_line, language, signature)`` and
    ``imports(file, module, line, resolved_file)`` (``resolved_file`` is NULL when the import is
    external or the language's resolver has no manifest to resolve against). JOIN them on
    ``file`` to find, e.g., which of a file's own symbols use a given import:
    ``SELECT s.symbol, i.module FROM symbols s JOIN imports i ON s.file = i.file``.
    """
    import sqlite3
    import time

    from tensor_grep.cli import lang_registry
    from tensor_grep.cli.repo_map import (
        _SUPPORTED_FILE_DEPENDENCY_LANGUAGES,
        _imports_with_lines_for_path,
        _infer_project_root,
        _max_parse_bytes,
        build_repo_map,
    )
    from tensor_grep.cli.repo_map import _resolve_raw_import_entry as resolve_raw_import_entry

    def _looks_like_sql(text: str) -> bool:
        t = text.strip()
        while True:
            if t.startswith("--"):
                _, _, t = t.partition("\n")
                t = t.strip()
            elif t.startswith("/*"):
                _, _, t = t.partition("*/")
                t = t.strip()
            else:
                break
        return t.upper().startswith(("SELECT", "WITH", "EXPLAIN", "PRAGMA", "VALUES"))

    if arg2 is None:
        path, query = ".", arg1
    else:
        p1_exists = Path(arg1).expanduser().exists()
        p2_exists = Path(arg2).expanduser().exists()
        if p1_exists and not p2_exists:
            path, query = arg1, arg2
        elif p2_exists and not p1_exists:
            path, query = arg2, arg1
        elif _looks_like_sql(arg1) and not _looks_like_sql(arg2):
            path, query = arg2, arg1
        else:
            path, query = arg1, arg2

    target_path = Path(path).expanduser().resolve()
    if not target_path.exists():
        err_msg = f"Path not found: {path}"
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "error": err_msg,
                        "rows": [],
                        "count": 0,
                        "truncated": False,
                        "result_incomplete": False,
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(err_msg, err=True)
        raise typer.Exit(code=1)

    if len(query) > 10_000:
        err_msg = f"SQL query exceeds maximum length of 10000 characters (length: {len(query)})"
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "error": err_msg,
                        "rows": [],
                        "count": 0,
                        "truncated": False,
                        "result_incomplete": False,
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(err_msg, err=True)
        raise typer.Exit(code=1)

    if _has_multiple_sql_statements(query):
        err_msg = "Multiple statements are not permitted. Execute a single query."
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "error": err_msg,
                        "rows": [],
                        "count": 0,
                        "truncated": False,
                        "result_incomplete": False,
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(err_msg, err=True)
        raise typer.Exit(code=1)

    deadline_monotonic = time.monotonic() + scan_deadline
    repo_map = build_repo_map(
        target_path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
    )
    from tensor_grep.cli import main as cli_main

    # The shared predicate also catches a --max-repo-files cap (scan_limit.possibly_truncated),
    # which a bare partial/result_incomplete read misses.
    map_scan_incomplete = cli_main._scan_incomplete(repo_map) or bool(
        repo_map.get("result_incomplete")
    )

    raw_symbols = repo_map.get("symbols", [])
    symbol_records = []
    lang_map = {
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".java": "java",
        ".php": "php",
        ".cs": "csharp",
    }

    def _norm_path(raw: str) -> str:
        # Sol audit round 2, finding 3: `resolved_file` must use the EXACT same path form as
        # `symbols.file`/`imports.file`, or `JOIN symbols s ON s.file = i.resolved_file` silently
        # returns zero rows even when resolution correctly found the file. Normalizing every
        # `file` column (both tables) through the identical `Path(...).resolve()` call the
        # resolver itself uses (`repo_map._resolved_path_str`) makes the three columns
        # byte-identical for the same on-disk file, regardless of which code path produced the
        # string first.
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw

    for item in raw_symbols:
        if not isinstance(item, dict):
            continue
        file_val = _norm_path(str(item.get("file", "")))
        sym_val = str(item.get("name") or item.get("symbol", ""))
        kind_val = str(item.get("kind", ""))
        line_val = int(item.get("line") or item.get("start_line", 0))
        end_line_val = int(item.get("end_line") or line_val)
        lang_val = item.get("language")
        if not lang_val:
            ext = Path(file_val).suffix.lower()
            lang_val = lang_map.get(ext, ext.lstrip(".") or "unknown")
        sig_val = item.get("signature")
        symbol_records.append((
            file_val,
            sym_val,
            kind_val,
            line_val,
            end_line_val,
            lang_val,
            sig_val,
        ))

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("""
            CREATE TABLE symbols (
                file TEXT NOT NULL,
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                language TEXT NOT NULL,
                signature TEXT
            );
        """)
        conn.execute("CREATE INDEX idx_symbols_file ON symbols(file);")
        conn.execute("CREATE INDEX idx_symbols_kind ON symbols(kind);")
        conn.execute("CREATE INDEX idx_symbols_name ON symbols(symbol);")

        conn.executemany(
            "INSERT INTO symbols (file, symbol, kind, line, end_line, language, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
            symbol_records,
        )

        conn.execute("""
            CREATE TABLE imports (
                file TEXT NOT NULL,
                module TEXT NOT NULL,
                line INTEGER NOT NULL,
                resolved_file TEXT
            );
        """)
        conn.execute("CREATE INDEX idx_imports_file ON imports(file);")
        conn.execute("CREATE INDEX idx_imports_module ON imports(module);")
        conn.commit()

        # Sol audit round 3: connection-wide limits and the query deadline/progress handler must
        # be in place BEFORE ANY statement is compiled on this connection -- including the
        # `EXPLAIN`-only detection probe below. Applying them only before the REAL execution left
        # `_detect_imports_referenced`'s compile running under SQLite's much larger DEFAULT
        # limits (unbounded `SQLITE_LIMIT_SQL_LENGTH`, 2000-column `SQLITE_LIMIT_COLUMN`) and with
        # no progress-handler interrupt at all, so a resource-shaped query could make the
        # detection compile itself do unbounded work before the connection was ever constrained.
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 100)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 10_000)

        # Sol audit round 5: this FIRST install exists only to bound the detection probe and the
        # imports pass below (so neither can run unbounded on this connection) -- its own
        # `deadline_tripped` flag is discarded. The REAL query gets a FRESH clock, re-installed
        # right before it runs (see the second `_install_query_deadline_handler` call below):
        # otherwise `--deadline` would silently also have to cover the probe's compile time and
        # the imports pass's row INSERTs (real SQLite VM steps), shrinking the query's own budget
        # by however long those happened to take.
        _install_query_deadline_handler(conn, deadline)

        # Perf finding (CEO round 3 on #1175): populating `imports` unconditionally taxed EVERY
        # `tg sql` call +90% (measured on src/tensor_grep), including a bare symbols-only SELECT.
        # `_detect_imports_referenced` asks SQLite's own authorizer whether *query* actually
        # touches `imports` (through any subquery/CTE/alias) BEFORE running the extraction pass;
        # `_run_imports_pass` -- the pass itself -- only runs when it does. Per-instruction:
        # imports incompleteness is reported ONLY when the pass actually ran.
        imports_referenced = _detect_imports_referenced(conn, query)
        imports_deadline_hit = False
        imports_unsupported_files_hit = False
        if imports_referenced:
            import_records, imports_deadline_hit, imports_unsupported_files_hit = _run_imports_pass(
                repo_map,
                deadline_monotonic,
                lang_registry_module=lang_registry,
                imports_with_lines_for_path=_imports_with_lines_for_path,
                infer_project_root=_infer_project_root,
                max_parse_bytes=_max_parse_bytes,
                resolve_raw_import_entry=resolve_raw_import_entry,
                supported_languages=_SUPPORTED_FILE_DEPENDENCY_LANGUAGES,
                norm_path=_norm_path,
            )
            conn.executemany(
                "INSERT INTO imports (file, module, line, resolved_file) VALUES (?, ?, ?, ?)",
                import_records,
            )
            conn.commit()

        imports_incomplete = imports_deadline_hit or imports_unsupported_files_hit

        # Sol R4: combine BEFORE the text banner, the JSON flag, and the exit-2 gate below -- an
        # imports-only cutoff must read exactly like any other scan incompleteness, not a silent
        # partial `imports` table under a `result_incomplete: false` payload.
        scan_incomplete = map_scan_incomplete or imports_incomplete

        # Sol audit round 2, finding 1: a machine-readable `incomplete_reason` list, not just the
        # human text banner -- an agent branching on JSON must be able to tell "the repo scan
        # itself was capped" apart from "the imports pass specifically hit its deadline" apart
        # from "some files were honestly unscannable for imports", since each implies a different
        # remediation.
        incomplete_reasons: list[str] = []
        if map_scan_incomplete:
            incomplete_reasons.append(
                cli_main._scan_truncation_warning(repo_map) or "repository scan was incomplete"
            )
        if imports_deadline_hit:
            incomplete_reasons.append("imports_deadline")
        if imports_unsupported_files_hit:
            incomplete_reasons.append("imports_unsupported_files")

        conn.set_authorizer(_sql_read_only_authorizer)

        # Sol audit round 5: the guard handler installed above (before the detection probe and
        # the imports pass, so THAT compile stays bounded) was also the one the REAL query's
        # `--deadline` was measured against -- so the probe's own compile time, and the imports
        # pass's row INSERTs (real SQLite VM steps), silently ate into the query's budget before
        # the query itself ever ran. Re-installing it HERE resets `query_deadline_monotonic` to
        # start fresh at the moment the real query begins, so `--deadline` bounds only the query,
        # never the work that happens to run before it on the same connection.
        deadline_tripped = _install_query_deadline_handler(conn, deadline)

        try:
            cursor = conn.execute(query)
            if cursor.description:
                col_names = [d[0] for d in cursor.description]
                seen_cols: set[str] = set()
                dup_cols: list[str] = []
                for c in col_names:
                    if c in seen_cols and c not in dup_cols:
                        dup_cols.append(c)
                    seen_cols.add(c)
                if dup_cols:
                    err_msg = (
                        f"Query contains duplicate column name(s): {', '.join(repr(c) for c in dup_cols)}. "
                        "Use distinct column aliases."
                    )
                    if json_output:
                        typer.echo(
                            json.dumps(
                                {
                                    "error": err_msg,
                                    "rows": [],
                                    "count": 0,
                                    "truncated": False,
                                    "result_incomplete": False,
                                },
                                indent=2,
                            )
                        )
                    else:
                        typer.echo(err_msg, err=True)
                    raise typer.Exit(code=1)
            else:
                col_names = []

            MAX_PAYLOAD_BYTES = MAX_SQL_PAYLOAD_BYTES
            rows: list[dict[str, Any]] = []
            output_truncated = False
            # Account for JSON envelope overhead (query, path, count, formatting indent)
            accumulated_bytes = min(2048, max(0, MAX_PAYLOAD_BYTES // 4))

            while True:
                row_tuple = cursor.fetchone()
                if row_tuple is None:
                    break
                row_dict = dict(zip(col_names, row_tuple, strict=False))
                # Bound check using indented serialization overhead to guarantee strict 5MB adherence
                row_bytes = len(json.dumps(row_dict, indent=4, default=str).encode("utf-8")) + 64
                if len(rows) < limit:
                    if accumulated_bytes + row_bytes > MAX_PAYLOAD_BYTES:
                        output_truncated = True
                        break
                    accumulated_bytes += row_bytes
                    rows.append(row_dict)
                else:
                    output_truncated = True
                    break

        except sqlite3.Error as exc:
            is_deadline = (
                deadline_tripped["hit"]
                or getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
            )
            if is_deadline:
                payload = {
                    "error": "query_deadline_exceeded",
                    "deadline_exceeded": True,
                    "result_incomplete": True,
                    "partial": True,
                    "rows": [],
                    "count": 0,
                    "truncated": False,
                }
                if json_output:
                    typer.echo(json.dumps(payload, indent=2))
                else:
                    typer.echo(
                        "Error: query deadline exceeded (query took longer than allocated time)",
                        err=True,
                    )
                raise typer.Exit(code=2) from exc
            else:
                err_msg = str(exc)
                if (
                    isinstance(exc, sqlite3.ProgrammingError)
                    and "one statement at a time" in err_msg.lower()
                ):
                    err_msg = "Multiple statements are not permitted. Execute a single query."
                if json_output:
                    typer.echo(
                        json.dumps(
                            {
                                "error": err_msg,
                                "rows": [],
                                "count": 0,
                                "truncated": False,
                                "result_incomplete": False,
                            },
                            indent=2,
                        )
                    )
                else:
                    typer.echo(f"SQL error: {err_msg}", err=True)
                raise typer.Exit(code=1) from exc

    finally:
        conn.close()

    is_incomplete = scan_incomplete or output_truncated
    payload = {
        "query": query,
        "path": str(path),
        "count": len(rows),
        "rows": rows,
        "truncated": output_truncated,
        "result_incomplete": is_incomplete,
        "partial": is_incomplete,
    }
    if scan_incomplete:
        payload["scan_incomplete"] = True
        payload["scan_truncated"] = True
        payload["incomplete_reason"] = incomplete_reasons

    if json_output:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        while len(encoded) + 1 > MAX_PAYLOAD_BYTES and rows:
            rows.pop()
            output_truncated = True
            is_incomplete = True
            payload["rows"] = rows
            payload["count"] = len(rows)
            payload["truncated"] = True
            payload["result_incomplete"] = True
            payload["partial"] = True
            encoded = json.dumps(payload, indent=2).encode("utf-8")
        typer.echo(encoded.decode("utf-8"))
    else:
        # LEADING stdout disclosure (task #329 rule): the table below is partial repository data.
        # Sol audit round 2, finding 2: name WHICH table is partial -- an imports-only cutoff
        # (map_scan_incomplete False) must never claim "the symbols table", which would send a
        # reader investigating the wrong half of the sandbox.
        if scan_incomplete and not cli_main._emit_scan_incompleteness_banner(repo_map):
            if imports_incomplete and map_scan_incomplete:
                table_desc = "symbols and imports tables hold"
            elif imports_incomplete:
                table_desc = "imports table holds"
            else:
                table_desc = "symbols table holds"
            typer.echo(
                f"INCOMPLETE RESULT: repository scan was incomplete; the {table_desc} "
                "partial repository data."
            )
        if not rows:
            if output_truncated:
                typer.echo("(0 rows [truncated])")
            else:
                typer.echo("0 rows returned.")
        else:
            headers = list(rows[0].keys())
            header_overhead = (
                sum(len(str(h).encode("utf-8")) for h in headers) + len(headers) * 3
            ) * 2 + 50
            if header_overhead > MAX_PAYLOAD_BYTES - 1:
                output_truncated = True
                is_incomplete = True
                typer.echo("(output [truncated]: table headers exceed payload limit)")
            else:
                widths = {h: len(str(h)) for h in headers}
                for r in rows:
                    for h in headers:
                        widths[h] = max(widths[h], len(str(r.get(h, ""))))

                header_line = " | ".join(str(h).ljust(widths[h]) for h in headers)
                sep_line = "-+-".join("-" * widths[h] for h in headers)

                if (
                    len(header_line.encode("utf-8")) + len(sep_line.encode("utf-8")) + 50
                    > MAX_PAYLOAD_BYTES - 1
                ):
                    output_truncated = True
                    is_incomplete = True
                    widths = {h: len(str(h)) for h in headers}
                    header_line = " | ".join(str(h).ljust(widths[h]) for h in headers)
                    sep_line = "-+-".join("-" * widths[h] for h in headers)
                    rendered_lines = [header_line, sep_line, "(0 rows [truncated])"]
                else:
                    rendered_lines = [header_line, sep_line]
                    current_bytes = (
                        len(header_line.encode("utf-8")) + len(sep_line.encode("utf-8")) + 2
                    )
                    emitted_rows = 0
                    for r in rows:
                        row_line = " | ".join(str(r.get(h, "")).ljust(widths[h]) for h in headers)
                        line_bytes = len(row_line.encode("utf-8")) + 1
                        if current_bytes + line_bytes + 50 > MAX_PAYLOAD_BYTES - 1:
                            output_truncated = True
                            is_incomplete = True
                            break
                        rendered_lines.append(row_line)
                        current_bytes += line_bytes
                        emitted_rows += 1

                    summary_line = f"({emitted_rows} row{'s' if emitted_rows != 1 else ''}{' [truncated]' if output_truncated else ''})"
                    rendered_lines.append(summary_line)

                full_text = "\n".join(rendered_lines)
                while (
                    len(full_text.encode("utf-8")) + 1 > MAX_PAYLOAD_BYTES
                    and len(rendered_lines) > 3
                ):
                    output_truncated = True
                    is_incomplete = True
                    rendered_lines.pop(len(rendered_lines) - 2)
                    summary_idx = len(rendered_lines) - 1
                    rendered_lines[summary_idx] = f"({len(rendered_lines) - 3} rows [truncated])"
                    full_text = "\n".join(rendered_lines)

                if len(full_text.encode("utf-8")) + 1 > MAX_PAYLOAD_BYTES:
                    output_truncated = True
                    is_incomplete = True
                    full_text = "(output [truncated]: payload limit exceeded)"

                typer.echo(full_text)

    if is_incomplete:
        raise typer.Exit(code=2)
