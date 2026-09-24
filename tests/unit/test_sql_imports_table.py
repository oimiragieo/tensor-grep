"""`tg sql`'s `imports` table (SLICE 4, resilient-cooking-cupcake plan).

Adds `imports(file, module, line, resolved_file NULL)` to the in-memory SQLite sandbox that
`tg sql` already exposes for `symbols`. Rows come from `repo_map._imports_with_lines_for_path`
(line-aware raw extraction, 10 supported languages) resolved via
`repo_map._resolve_raw_import_entry` (module-string -> target-file, honestly unresolved when the
resolver has no manifest to resolve against). The shared repo-map payload gains no new keys
(Sol R3 finding 2): `sql_query.py` owns this pass itself, over `repo_map["files"]`, under the
SAME `--scan-deadline` the symbols pass already respects (Sol R4/R5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sql_imports_join_symbols(tmp_path: Path) -> None:
    """A JOIN of symbols x imports on file finds the importer's own defined symbol."""
    proj = tmp_path / "proj"
    _write(
        proj / "pkg" / "__init__.py",
        "",
    )
    _write(proj / "pkg" / "util.py", "def helper():\n    pass\n")
    _write(
        proj / "pkg" / "main.py",
        "from pkg import util\n\n\ndef entrypoint():\n    return util.helper()\n",
    )

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT s.symbol, i.module FROM symbols s JOIN imports i ON s.file = i.file "
            "WHERE s.symbol = 'entrypoint'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is False
    assert payload["count"] == 1
    assert payload["rows"][0]["symbol"] == "entrypoint"
    assert payload["rows"][0]["module"] == "pkg"


def test_sql_imports_unresolved_row_kept_with_null(tmp_path: Path) -> None:
    """An import of a genuinely external module is kept, with `resolved_file` NULL."""
    proj = tmp_path / "proj"
    _write(proj / "main.py", "import totally_external_package_xyz\n")

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT module, resolved_file FROM imports WHERE module = "
            "'totally_external_package_xyz'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["rows"][0]["module"] == "totally_external_package_xyz"
    assert payload["rows"][0]["resolved_file"] is None


def test_sql_imports_resolved_file_joins_symbols_file(tmp_path: Path) -> None:
    """`resolved_file` must use the EXACT same path form as `symbols.file`/`imports.file`, so a
    JOIN from an importer's row to the imported module's OWN symbol rows actually matches (Sol
    audit round 2, finding 3) -- not merely that resolution produced a plausible-looking string."""
    proj = tmp_path / "proj"
    _write(proj / "pkg" / "__init__.py", "")
    _write(proj / "pkg" / "util.py", "def helper():\n    pass\n")
    _write(proj / "pkg" / "main.py", "import pkg.util\n")

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT s.symbol, s.file AS defining_file FROM imports i "
            "JOIN symbols s ON s.file = i.resolved_file "
            "WHERE i.module = 'pkg.util'",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is False
    assert payload["count"] == 1
    assert payload["rows"][0]["symbol"] == "helper"
    assert payload["rows"][0]["defining_file"].endswith("util.py")


def test_sql_imports_no_imports_negative_control(tmp_path: Path) -> None:
    """A repo with source files but zero import statements yields zero import rows, complete."""
    proj = tmp_path / "proj"
    _write(proj / "lonely.py", "def standalone():\n    return 1\n")

    result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert payload["result_incomplete"] is False


def test_sql_imports_non_code_files_do_not_make_the_table_incomplete(tmp_path: Path) -> None:
    """Control arm for the Kotlin test below: README/config/text files have no imports, so they
    are outside the table's universe. Before this, one `commands.txt` flagged every `imports`
    query INCOMPLETE with exit 2 -- dogfooded on the published 1.123.0 wheel against
    src/tensor_grep/cli, i.e. nearly every real repository read as partial."""
    proj = tmp_path / "proj"
    _write(proj / "app.py", "import os\n\ndef f():\n    pass\n")
    _write(proj / "README.md", "# docs\n")
    _write(proj / "commands.txt", "tg search\n")
    _write(proj / "config.toml", "[tool]\n")

    result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is False, payload
    assert "incomplete_reason" not in payload or not payload["incomplete_reason"], payload
    assert {row["module"] for row in payload["rows"]} == {"os"}


def test_sql_imports_unsupported_language_file_flagged(tmp_path: Path) -> None:
    """A file with no registered language spec (e.g. Kotlin) is honestly disclosed, not silently
    treated as "this file has zero imports"."""
    proj = tmp_path / "proj"
    _write(proj / "app.py", "def f():\n    pass\n")
    _write(proj / "Main.kt", 'fun main() { println("hi") }\n')

    json_result = runner.invoke(app, ["sql", str(proj), "SELECT * FROM imports", "--json"])
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True
    # Sol audit round 2, finding 1: a machine-readable reason, distinguishable from a map-scan
    # cap or a deadline cutoff.
    assert payload["incomplete_reason"] == ["imports_unsupported_files"]

    text_result = runner.invoke(app, ["sql", str(proj), "SELECT * FROM imports"])
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout
    # Sol audit round 2, finding 2: the banner must name the `imports` table specifically -- the
    # `symbols` scan itself was complete here, only the imports pass hit an unscannable file.
    assert "imports table holds" in text_result.stdout
    assert "symbols table holds" not in text_result.stdout


def _clock_expiring_after(n_unexpired_calls: int):
    """A deterministic counter clock: returns ``0.0`` (never expired, since `deadline_monotonic`
    is always a positive real number) for the first *n_unexpired_calls* calls, then ``inf``
    (always expired) forever after. Independent of `deadline_monotonic`'s actual magnitude, so
    the test never has to know or control it -- only the CALL COUNT at which the cutoff lands.
    """
    state = {"n": 0}

    def clock() -> float:
        state["n"] += 1
        return 0.0 if state["n"] <= n_unexpired_calls else float("inf")

    return clock


def _inject_imports_clock(monkeypatch: pytest.MonkeyPatch, clock) -> None:
    """Sol audit round 3 (tests-only): inject the clock ONLY into `_run_imports_pass`, not into
    `time.monotonic` globally -- a global patch also slows/derails the repo-map SCAN this pass
    runs after, making "did the cutoff land mid-file" a function of real wall-clock timing rather
    than of the exact resolve call the test means to control. Wraps `_run_imports_pass` itself
    (this module's own entry point, not a `repo_map` symbol -- mind the bare-call ratchet) so the
    real pass logic still runs, just against the deterministic clock instead of `time.monotonic`.
    """
    import tensor_grep.cli.sql_query as sql_query_module

    real_pass = sql_query_module._run_imports_pass

    def wrapped(*args, **kwargs):
        kwargs["clock"] = clock
        return real_pass(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_run_imports_pass", wrapped)


def test_sql_imports_deadline_hit_mid_import_pass_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clock that is ALREADY expired when the import pass starts (the repo-map build itself
    runs on the real clock, unaffected) must still surface as INCOMPLETE (banner on text, flag on
    JSON, exit 2 on both) -- Sol R4."""
    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")
    _write(proj / "b.py", "import sys\n")

    _inject_imports_clock(monkeypatch, _clock_expiring_after(0))

    json_result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json", "--scan-deadline", "30"],
    )
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True
    assert "imports_deadline" in payload["incomplete_reason"]
    # The pass expired before resolving anything.
    assert payload["count"] == 0

    text_result = runner.invoke(
        app, ["sql", str(proj), "SELECT * FROM imports", "--scan-deadline", "30"]
    )
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout


def test_sql_imports_deadline_hit_within_a_single_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cutoff can land WITHIN one file's own import list (between two resolve calls for the
    SAME file), not only between files (Sol R5) -- driven by an exact, deterministic call count,
    not a real sleep racing a real deadline (Sol audit round 3)."""
    proj = tmp_path / "proj"
    lines = "\n".join(f"import mod_{i}" for i in range(60))
    _write(proj / "many_imports.py", lines + "\ndef marker_symbol():\n    pass\n")

    # Call sequence inside `_run_imports_pass` for this ONE-file fixture: call #1 is the
    # file-level deadline check, then one call per raw entry BEFORE it is resolved. Allowing
    # exactly 21 unexpired calls (the file check + 20 per-entry checks) lets entries 0..19
    # resolve, then expires on the 21st entry's check (raw index 20) -- landing the cutoff
    # provably mid-file, never at a file boundary.
    _inject_imports_clock(monkeypatch, _clock_expiring_after(21))

    json_result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT * FROM imports", "--json", "--scan-deadline", "30"],
    )
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert "imports_deadline" in payload["incomplete_reason"]
    # Proves the repo-map BUILD (which runs on the REAL clock, before the import pass, and is
    # never touched by the injected clock) reached this file at all -- `marker_symbol` only
    # exists in the payload if the symbols scan of `many_imports.py` completed.
    symbols_result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "SELECT symbol FROM symbols WHERE symbol = 'marker_symbol'",
            "--json",
            "--scan-deadline",
            "30",
        ],
    )
    symbols_payload = json.loads(symbols_result.stdout)
    assert symbols_payload["count"] == 1, symbols_payload
    # Deterministic, exact: 20 of the 60 entries resolved before the cutoff -- not merely "some".
    assert payload["count"] == 20, payload


def test_sql_write_to_imports_refused(tmp_path: Path) -> None:
    """The read-only authorizer covers `imports` exactly like `symbols`: no INSERT/DDL."""
    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")

    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            "INSERT INTO imports VALUES ('x.py', 'os', 1, NULL)",
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert "error" in payload


def test_sql_symbols_only_query_does_not_run_imports_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CEO round 3 on #1175: a symbols-only query must not pay the imports-pass tax at all --
    spy on the pass's OWN entry point (`sql_query._run_imports_pass`, defined in this module, not
    a `repo_map` symbol -- mind the bare-call ratchet) and assert it is never called."""
    import tensor_grep.cli.sql_query as sql_query_module

    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n\n\ndef standalone():\n    return 1\n")

    calls: list[object] = []
    real_pass = sql_query_module._run_imports_pass

    def spy(*args, **kwargs):
        calls.append(args)
        return real_pass(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_run_imports_pass", spy)

    result = runner.invoke(
        app,
        ["sql", str(proj), "SELECT symbol FROM symbols WHERE symbol = 'standalone'", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["result_incomplete"] is False
    assert calls == [], "a symbols-only query must never invoke the imports pass"
    # No imports-side incompleteness may be reported when the pass never ran.
    assert "incomplete_reason" not in payload


@pytest.mark.parametrize(
    "query",
    [
        # Plain reference.
        "SELECT module FROM imports",
        # A view-less alias -- the authorizer resolves the REAL table name, not the alias.
        "SELECT i.module FROM imports AS i",
        # Referenced only through a subquery.
        "SELECT module FROM (SELECT module FROM imports)",
        # Referenced only through a CTE.
        "WITH x AS (SELECT module FROM imports) SELECT module FROM x",
    ],
)
def test_sql_imports_referenced_via_alias_subquery_or_cte_runs_the_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """Every indirection the detector must see through: a bare reference, an alias, a subquery,
    and a CTE all count as "the query references imports" and must run the real pass."""
    import tensor_grep.cli.sql_query as sql_query_module

    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")

    calls: list[object] = []
    real_pass = sql_query_module._run_imports_pass

    def spy(*args, **kwargs):
        calls.append(args)
        return real_pass(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_run_imports_pass", spy)

    result = runner.invoke(app, ["sql", str(proj), query, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(calls) == 1, f"query {query!r} must invoke the imports pass exactly once"
    assert any(row["module"] == "os" for row in payload["rows"])


def test_sql_over_length_query_rejected_before_the_imports_probe_ever_compiles_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol audit round 3: connection limits (`SQLITE_LIMIT_SQL_LENGTH`/`SQLITE_LIMIT_COLUMN`) and
    the progress handler/deadline must be applied BEFORE `_detect_imports_referenced` ever
    compiles anything -- an over-length query must be rejected with the SAME error as before that
    reorder, and the detection probe must never even see it (spy on the module's own entry
    point, not a `repo_map` symbol -- mind the bare-call ratchet)."""
    import tensor_grep.cli.sql_query as sql_query_module

    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")

    calls: list[object] = []
    real_detect = sql_query_module._detect_imports_referenced

    def spy(*args, **kwargs):
        calls.append(args)
        return real_detect(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_detect_imports_referenced", spy)

    over_length_query = "SELECT " + "1," * 5000 + "1"
    assert len(over_length_query) > 10_000

    result = runner.invoke(app, ["sql", str(proj), over_length_query, "--json"])
    # ci-local (Linux, Python 3.12, system sqlite) finding: stdout came back EMPTY here because
    # `_safe_path_exists`'s predecessor called bare `Path(candidate).exists()` on the
    # over-length query STRING (routing logic probing "is this arg a path?"), which raises an
    # uncaught `OSError(ENAMETOOLONG)` on Linux (not in `pathlib._IGNORED_ERRNOS`) but not on
    # Windows -- self-diagnosing on the next platform-specific instrument failure: dump BOTH
    # `result.output` (empty stdout reads as nothing here) AND `result.exception`.
    assert result.exit_code == 1, (result.output, result.exception)
    assert result.stdout, (
        "stdout is EMPTY -- an exception likely escaped before any JSON was written; "
        f"exception={result.exception!r} output={result.output!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["error"] == (
        f"SQL query exceeds maximum length of 10000 characters (length: {len(over_length_query)})"
    )
    assert calls == [], "an over-length query must never reach the imports-reference probe"


def test_sql_path_routing_survives_an_ename_too_long_style_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the ci-local Linux failure HERMETICALLY, on any platform: `Path.exists()`
    raising `OSError` for a candidate too long for the OS's path-length limit is exactly the
    class `pathlib._IGNORED_ERRNOS` does NOT cover (it swallows ENOENT/ENOTDIR/EBADF, not
    ENAMETOOLONG) -- so the real defect is platform-specific (Linux only) but the fix is
    verifiable everywhere by injecting the exact OSError Linux raises, rather than trying to
    grow a real 10,000+ char path on disk."""
    import errno

    proj = tmp_path / "proj"
    _write(proj / "a.py", "import os\n")

    over_length_query = "SELECT " + "1," * 5000 + "1"

    real_exists = Path.exists

    def _fake_exists(self, *args, **kwargs):
        if str(self) == over_length_query:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", _fake_exists)

    result = runner.invoke(app, ["sql", str(proj), over_length_query, "--json"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert result.stdout, (
        f"stdout is EMPTY; exception={result.exception!r} output={result.output!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["error"] == (
        f"SQL query exceeds maximum length of 10000 characters (length: {len(over_length_query)})"
    )


def test_sql_target_path_not_found_stays_exit_1_with_path_not_found(tmp_path: Path) -> None:
    """Control arm for the split below: a target path that GENUINELY does not exist (ENOENT,
    already swallowed internally by `Path.exists()` -- it never even reaches our `except
    OSError` handler) must keep the ORIGINAL `path_not_found` shape and exit code, unaffected by
    the `path_unreadable` split for real access failures."""
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()

    result = runner.invoke(app, ["sql", str(missing), "SELECT 1", "--json"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert result.stdout, f"stdout is EMPTY; exception={result.exception!r}"
    payload = json.loads(result.stdout)
    assert payload["error"] == f"Path not found: {missing}"
    assert "errno" not in payload


def _errno_classification_cases():
    import errno

    return [
        pytest.param(errno.EACCES, "Permission denied", "path_unreadable", None, id="EACCES"),
        pytest.param(errno.EPERM, "Operation not permitted", "path_unreadable", None, id="EPERM"),
        pytest.param(
            errno.ENAMETOOLONG, "File name too long", "invalid_path", None, id="ENAMETOOLONG"
        ),
        pytest.param(errno.EINVAL, "Invalid argument", "invalid_path", None, id="EINVAL"),
        pytest.param(
            errno.ELOOP, "Too many levels of symbolic links", "invalid_path", None, id="ELOOP"
        ),
        pytest.param(errno.ENOTDIR, "Not a directory", "invalid_path", None, id="ENOTDIR"),
        pytest.param(
            None,
            "The filename, directory name, or volume label syntax is incorrect",
            "invalid_path",
            123,
            id="winerror-123-invalid-name",
        ),
        pytest.param(
            None,
            "The filename or extension is too long",
            "invalid_path",
            206,
            id="winerror-206-too-long",
        ),
        # An errno this classification doesn't recognize (e.g. EIO, a hardware-level read
        # failure) must still be disclosed -- fail TOWARD `path_unreadable` with its own
        # errno/strerror, never silently swallowed or misclassified as `invalid_path`.
        pytest.param(errno.EIO, "Input/output error", "path_unreadable", None, id="EIO-fallback"),
    ]


@pytest.mark.parametrize(
    "errno_value, strerror, expected_error_code, winerror_value", _errno_classification_cases()
)
def test_sql_target_path_error_classified_by_errno(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    errno_value: int | None,
    strerror: str,
    expected_error_code: str,
    winerror_value: int | None,
) -> None:
    """Sol audit follow-up (final): the final target-path `OSError` is classified by errno --
    EACCES/EPERM (a real access-control failure) -> `path_unreadable`; ENAMETOOLONG/EINVAL/
    ELOOP/ENOTDIR (the path STRING is malformed for this OS) -- plus the Windows-only winerror
    123/206 for the identical shape -- -> `invalid_path`; anything else stays `path_unreadable`
    with its own errno/strerror rather than guessing. ALL exit 2, ALL structured JSON, ALL a
    clear text message. Hermetic (no real chmod / no real 10,000+ char path on disk): inject the
    exact `OSError` for `Path.exists()` on the resolved target path, scoped to the calling frame
    (`sql_query.py` only -- the routing probe's `_safe_path_exists` deliberately keeps its own
    blanket OSError->False fallback, per the finding's own instruction)."""
    import sys

    denied = tmp_path / "denied-dir"
    denied.mkdir()
    resolved_target = str(denied.resolve())

    real_exists = Path.exists

    def _fake_exists(self, *args, **kwargs):
        caller = sys._getframe(1).f_code.co_filename
        if str(self) == resolved_target and caller.endswith("sql_query.py"):
            exc = OSError(errno_value, strerror)
            if winerror_value is not None:
                exc.winerror = winerror_value  # type: ignore[attr-defined]
            raise exc
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", _fake_exists)

    json_result = runner.invoke(app, ["sql", str(denied), "SELECT 1", "--json"])
    assert json_result.exit_code == 2, (json_result.output, json_result.exception)
    assert json_result.stdout, f"stdout is EMPTY; exception={json_result.exception!r}"
    payload = json.loads(json_result.stdout)
    assert payload["error"] == expected_error_code
    assert payload["path"] == str(denied)
    assert payload["errno"] == errno_value
    assert payload["strerror"] == strerror
    assert payload["result_incomplete"] is True

    text_result = runner.invoke(app, ["sql", str(denied), "SELECT 1"])
    assert text_result.exit_code == 2, (text_result.output, text_result.exception)
    expected_label = (
        "Path unreadable" if expected_error_code == "path_unreadable" else "Invalid path"
    )
    assert expected_label in text_result.output
    assert strerror in text_result.output


def _make_shared_counter_clock(increment: float = 1.0):
    """A deterministic clock: every call (from ANY caller) advances a shared counter by
    *increment* and returns the new value. Used to prove the query-deadline clock RESETS right
    before the real query runs, without any real wall-clock sleep -- the SAME counter is shared
    across `_run_imports_pass`'s deadline checks and `_install_query_deadline_handler`'s own
    checks, so "the imports pass consumed N clock ticks" and "the query deadline started counting
    from tick N" are the exact same number, not two independently-drifting real clocks.
    """
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += increment
        return state["t"]

    return clock


def _inject_shared_clock_into_pass_and_handler(monkeypatch: pytest.MonkeyPatch, clock) -> None:
    """Wrap BOTH `_run_imports_pass` and `_install_query_deadline_handler` (this module's own
    entry points, not `repo_map` symbols -- mind the bare-call ratchet) so every clock read
    either one performs comes from the SAME shared counter."""
    import tensor_grep.cli.sql_query as sql_query_module

    real_pass = sql_query_module._run_imports_pass
    real_install = sql_query_module._install_query_deadline_handler

    def wrapped_pass(*args, **kwargs):
        kwargs["clock"] = clock
        return real_pass(*args, **kwargs)

    def wrapped_install(*args, **kwargs):
        kwargs["clock"] = clock
        return real_install(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_run_imports_pass", wrapped_pass)
    monkeypatch.setattr(sql_query_module, "_install_query_deadline_handler", wrapped_install)


def test_sql_imports_pass_consuming_more_than_deadline_does_not_interrupt_a_fast_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol audit round 5: the imports pass runs on the SAME connection as the real query, before
    it. Its own clock reads (many, for a file with many imports) must NOT count against the
    query's `--deadline` -- the deadline clock is re-installed (reset) right before the real
    query executes. Deterministic: a shared counter clock proves the reset by construction,
    not by racing a real sleep against a real deadline."""
    proj = tmp_path / "proj"
    lines = "\n".join(f"import mod_{i}" for i in range(30))
    _write(proj / "many_imports.py", lines + "\n")

    clock = _make_shared_counter_clock(increment=1.0)
    _inject_shared_clock_into_pass_and_handler(monkeypatch, clock)

    # The imports pass alone reads the shared clock ~31 times (1 file-level check + 30
    # per-entry checks) -- far more than the query's own `--deadline` of 5 "ticks". A query that
    # ALSO references `imports` (so the pass actually runs, via the `WHERE` subquery, without a
    # row-multiplying cross join) but is itself cheap enough to invoke the progress handler only
    # a handful of times (empirically ~1 call for 100 recursion steps, measured against the real
    # sqlite3 progress-handler cadence) must still complete successfully: the fresh baseline
    # installed right before it runs means only ITS OWN ticks count, not the pass's.
    fast_query_referencing_imports = (
        "WITH RECURSIVE r(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM r WHERE i < 100), "
        "bound(n) AS (SELECT count(*) + 999999 FROM imports) "
        "SELECT count(*) FROM r, bound WHERE r.i < bound.n"
    )
    result = runner.invoke(
        app,
        [
            "sql",
            str(proj),
            fast_query_referencing_imports,
            "--json",
            "--deadline",
            "5",
            "--scan-deadline",
            "30",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("error") is None, payload
    assert payload["result_incomplete"] is False


def test_sql_slow_query_still_interrupted_after_the_deadline_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sol audit round 5: the reset must not defeat real enforcement -- a query that is ITSELF
    slow (many progress-handler ticks past the fresh baseline) still gets interrupted. No imports
    pass involved here (the query never references `imports`), isolating the assertion to the
    re-installed handler alone."""
    proj = tmp_path / "proj"
    _write(proj / "a.py", "def f():\n    pass\n")

    clock = _make_shared_counter_clock(increment=1.0)
    _inject_shared_clock_into_pass_and_handler(monkeypatch, clock)

    slow_query = (
        "WITH RECURSIVE r(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM r WHERE i < 1000000) "
        "SELECT count(*) FROM r"
    )
    result = runner.invoke(
        app,
        ["sql", str(proj), slow_query, "--json", "--deadline", "5"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"] == "query_deadline_exceeded"
    assert payload["deadline_exceeded"] is True


def test_sql_large_imports_insert_is_never_interrupted_by_the_query_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol audit round 6: NO query-deadline progress handler may be live during the imports
    pass's `executemany`/`commit`. Proof: inject a clock into `_install_query_deadline_handler`
    that is ALREADY EXPIRED on its very first call. If a handler using that clock were installed
    before the imports pass (round 5's design), a large INSERT (empirically ~2 real
    progress-handler ticks for 300 rows, measured with a positive-control script) would raise an
    UNCAUGHT `sqlite3.OperationalError` from inside the pass -- outside the query's own
    `except sqlite3.Error` block, which only wraps `conn.execute(query)` below. With the handler
    installed exactly once, immediately before the real query, this clock cannot affect the pass
    at all: only the (fast) real query's own execution is bounded by it."""
    proj = tmp_path / "proj"
    lines = "\n".join(f"import mod_{i}" for i in range(300))
    _write(proj / "many_imports.py", lines + "\n")

    def already_expired_clock() -> float:
        return float("inf")

    import tensor_grep.cli.sql_query as sql_query_module

    real_install = sql_query_module._install_query_deadline_handler

    def wrapped_install(*args, **kwargs):
        kwargs["clock"] = already_expired_clock
        return real_install(*args, **kwargs)

    monkeypatch.setattr(sql_query_module, "_install_query_deadline_handler", wrapped_install)

    # Fast enough to invoke the progress handler ~0 times during the REAL query itself; the point
    # is to prove the large INSERT (which ran before this handler was even installed) was
    # unaffected, not to also test the query's own deadline enforcement (that is
    # `test_sql_slow_query_still_interrupted_after_the_deadline_reset`, above).
    fast_query = "SELECT count(*) AS n FROM imports"
    result = runner.invoke(
        app,
        ["sql", str(proj), fast_query, "--json", "--deadline", "0.1", "--scan-deadline", "30"],
    )
    assert result.exception is None, result.output
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rows"][0]["n"] == 300
    assert payload.get("error") is None


def _deny_stat_from_sql_query_only(monkeypatch: pytest.MonkeyPatch, denied_path: Path) -> None:
    """Hermetic seam injection (not `chmod`, which is unreliable on Windows and CI containers
    running as root): monkeypatch `Path.stat` itself, scoped to BOTH the exact denied path AND
    the calling frame (`sql_query.py`) -- mirrors `test_inventory.py`'s
    `_deny_stat_from_inventory_only`. Scoping to the caller frame reproduces the real defect (the
    file is walked -- inside the universe `tg sql`'s `imports` table claims to describe -- and a
    LATER per-file `stat()` fails: the ordinary TOCTOU window of a file deleted or its
    permissions changed mid-command) without also poisoning `stat()` calls made during the
    repo-map SCAN (`repo_map.py`/`repo_map_lang_python.py`), which would conflate this failure
    with an unrelated one.
    """
    import os as _os
    import sys as _sys

    real_stat = Path.stat
    target = _os.path.abspath(_os.fspath(denied_path))

    def _fake_stat(self, *args, **kwargs):
        caller = _sys._getframe(1).f_code.co_filename
        if _os.path.abspath(_os.fspath(self)) == target and caller.endswith("sql_query.py"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _fake_stat)


def test_sql_imports_unreadable_file_is_disclosed_not_silently_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ci-local finding: `_run_imports_pass`'s per-file `stat()` fell back to `file_size = 0` on
    `OSError` and kept going, silently reading an unreadable/vanished file as "genuinely has zero
    imports" -- the exact silent-loss class `test_silent_loss_census_ratchet.py` guards (the walk
    already reported this file, so it is inside the universe the `imports` table claims to
    describe). Fixed by threading `repo_map._UnreadablePathFlag` through the loop, the same shape
    `inventory.py`/`docs_coverage.py` already use."""
    proj = tmp_path / "proj"
    _write(proj / "good.py", "import os\n")
    denied = proj / "denied.py"
    _write(denied, "import sys\n")

    # CONTROL ARM -- both files' imports present, nothing incomplete. If this stops differing
    # from the treatment arm below, the fixture has stopped discriminating.
    clean_result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports", "--json"])
    assert clean_result.exit_code == 0, clean_result.output
    clean_payload = json.loads(clean_result.stdout)
    assert {row["module"] for row in clean_payload["rows"]} == {"os", "sys"}
    assert clean_payload["result_incomplete"] is False

    _deny_stat_from_sql_query_only(monkeypatch, denied)

    json_result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports", "--json"])
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    modules = {row["module"] for row in payload["rows"]}
    assert modules == {"os"}, (
        "precondition: the denied file's import must actually drop out, otherwise the "
        f"disclosure below is untested (got {modules})"
    )
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True
    assert "imports_unreadable_files" in payload["incomplete_reason"]
    assert payload["unreadable_paths_count"] == 1
    assert any(Path(p).name == "denied.py" for p in payload["unreadable_paths"])

    text_result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports"])
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout
    assert "imports table holds" in text_result.stdout


def _deny_read_bytes_from_sql_query_only(
    monkeypatch: pytest.MonkeyPatch, denied_path: Path
) -> None:
    """Second silent-loss path (Sol follow-up on 844e07f): `repo_map._imports_with_lines_for_path`
    reads the file itself and returns `[]` on `OSError` -- indistinguishable, to a caller, from
    "genuinely has zero imports". `stat()` can succeed while the SUBSEQUENT read still fails
    (TOCTOU, a permission change, a vanished file) -- this fixture denies ONLY `Path.read_bytes`,
    never `Path.stat`, so the precondition ("stat succeeds but read raises") is real, not assumed.
    Scoped to the calling frame (`sql_query.py`) exactly like `_deny_stat_from_sql_query_only`, so
    it cannot poison the repo-map SCAN's own file reads.
    """
    import os as _os
    import sys as _sys

    real_read_bytes = Path.read_bytes
    target = _os.path.abspath(_os.fspath(denied_path))

    def _fake_read_bytes(self, *args, **kwargs):
        caller = _sys._getframe(1).f_code.co_filename
        if _os.path.abspath(_os.fspath(self)) == target and caller.endswith("sql_query.py"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", _fake_read_bytes)


def test_sql_imports_read_failure_after_successful_stat_is_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol follow-up on 844e07f: a read failure AFTER a successful `stat()` must be disclosed
    exactly like a stat failure -- `_imports_with_lines_for_path`'s own internal `OSError` ->
    `[]` fallback is invisible to `_run_imports_pass`'s caller, so this pass reads the file
    itself first (inside the same `OSError` -> `_UnreadablePathFlag` handling) and never reaches
    the shared helper for a file it could not read."""
    proj = tmp_path / "proj"
    _write(proj / "good.py", "import os\n")
    denied = proj / "denied.py"
    _write(denied, "import sys\n")

    # CONTROL ARM -- both files' imports present, nothing incomplete.
    clean_result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports", "--json"])
    assert clean_result.exit_code == 0, clean_result.output
    clean_payload = json.loads(clean_result.stdout)
    assert {row["module"] for row in clean_payload["rows"]} == {"os", "sys"}
    assert clean_payload["result_incomplete"] is False

    _deny_read_bytes_from_sql_query_only(monkeypatch, denied)

    json_result = runner.invoke(app, ["sql", str(proj), "SELECT module FROM imports", "--json"])
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    modules = {row["module"] for row in payload["rows"]}
    assert modules == {"os"}, (
        "precondition: stat succeeded (the file is not size-capped/unsupported) but the read "
        f"still failed and dropped its import, otherwise the disclosure below is untested "
        f"(got {modules})"
    )
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True
    assert "imports_unreadable_files" in payload["incomplete_reason"]
    assert payload["unreadable_paths_count"] == 1
    assert any(Path(p).name == "denied.py" for p in payload["unreadable_paths"])
