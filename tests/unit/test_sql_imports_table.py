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
