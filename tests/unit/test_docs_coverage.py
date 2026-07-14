"""tg docs-coverage: source files not referenced by any governing doc (CLAUDE.md/README/AGENTS.md).

From v1.19.9 dogfood -- the reporter's "most valuable thing in this whole sweep." Reference-existence
only (not semantic), lenient path-or-basename match so it under-reports gaps rather than flooding.
"""

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.docs_coverage import (
    build_docs_coverage,
    build_docs_stale_references,
    render_docs_coverage_fix_markdown,
    render_docs_coverage_text,
    render_docs_stale_text,
)
from tensor_grep.cli.main import app


def test_stale_reference_flagged_when_cited_file_deleted(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(
        "See `src/exists.py` and `src/gone.py` for details.\n", encoding="utf-8"
    )
    payload = build_docs_stale_references(str(tmp_path))
    stale = {(item["doc"], item["reference"]) for item in payload["stale_references"]}
    assert ("CLAUDE.md", "src/gone.py") in stale  # deleted file, real parent dir -> stale
    assert all(ref != "src/exists.py" for _, ref in stale)  # existing file not flagged
    assert "src/gone.py" in render_docs_stale_text(payload)


def test_stale_ignores_fictional_path_without_real_parent(tmp_path):
    # Precision guard: an illustrative path whose DIRECTORY never existed is not "stale".
    (tmp_path / "CLAUDE.md").write_text(
        "Example: `some/imaginary/path.py` is how you would do it.\n", encoding="utf-8"
    )
    assert build_docs_stale_references(str(tmp_path))["stale_references"] == []


def test_stale_ignores_bare_basenames_and_urls(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "Run `pytest`, see `config.py`, visit `https://example.com/x.py`.\n", encoding="utf-8"
    )
    # bare basename (no separator) + a URL are never treated as repo paths
    assert build_docs_stale_references(str(tmp_path))["stale_references"] == []


def test_check_exits_nonzero_on_uncovered_and_prints_report(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bar.py").write_text("y = 2\n", encoding="utf-8")  # no doc -> uncovered
    result = CliRunner().invoke(app, ["docs-coverage", str(tmp_path), "--check"])
    assert result.exit_code == 1
    assert "bar.py" in result.output  # the report is still emitted before the non-zero exit


def test_check_exits_zero_when_all_covered(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bar.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("bar.py is the module.\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["docs-coverage", str(tmp_path), "--check"])
    assert result.exit_code == 0


def test_check_respects_ignore(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.stub.py").write_text("y = 2\n", encoding="utf-8")  # only an ignored stub
    result = CliRunner().invoke(
        app, ["docs-coverage", str(tmp_path), "--check", "--ignore", "*.stub.py"]
    )
    assert result.exit_code == 0  # ignored -> nothing uncovered -> gate passes


def test_check_stale_exits_nonzero(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("See `src/gone.py`.\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["docs-coverage", str(tmp_path), "--stale", "--check"])
    assert result.exit_code == 1


def test_stale_references_respects_ignore_glob(tmp_path):
    # audit #23: `--ignore` was silently dropped for `--stale` even though the `--check` help text
    # already promises "Respects --ignore" for both modes.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "examples").mkdir()  # real parent dir -> a real "stale" reference, not fictional
    (tmp_path / "CLAUDE.md").write_text(
        "See `src/gone.py` and `examples/example.stub.py` for details.\n", encoding="utf-8"
    )
    base = build_docs_stale_references(str(tmp_path))
    references = {item["reference"] for item in base["stale_references"]}
    assert {"src/gone.py", "examples/example.stub.py"} <= references

    filtered = build_docs_stale_references(str(tmp_path), ignore=("*.stub.py",))
    filtered_references = {item["reference"] for item in filtered["stale_references"]}
    assert "examples/example.stub.py" not in filtered_references
    assert "src/gone.py" in filtered_references
    assert filtered["applied_ignore"] == ["*.stub.py"]
    # the ignored reference is dropped entirely, not just unflagged -- references_checked shrinks too
    assert filtered["totals"]["references_checked"] < base["totals"]["references_checked"]


def test_docs_coverage_stale_cli_respects_ignore(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "examples").mkdir()  # real parent dir -> a real "stale" reference, not fictional
    (tmp_path / "CLAUDE.md").write_text(
        "See `examples/example.stub.py` for details.\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        [
            "docs-coverage",
            str(tmp_path),
            "--stale",
            "--check",
            "--ignore",
            "*.stub.py",
        ],
    )
    assert result.exit_code == 0, result.output  # ignored -> nothing stale -> gate passes


def test_docs_coverage_stale_and_fix_is_rejected(tmp_path):
    # audit #23: --fix's Markdown table renders build_docs_coverage's uncovered-files shape, which
    # does not exist in --stale mode -- previously a silent no-op (the table was never rendered,
    # with no signal that --fix had no effect).
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("See `src/gone.py`.\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["docs-coverage", str(tmp_path), "--stale", "--fix"])
    assert result.exit_code == 1
    assert "--fix" in result.output
    assert "--stale" in result.output


def test_exclusion_matches_relative_parts_not_ancestor_dirs(tmp_path):
    # Round-6 rank-2: a project checked out UNDER a dir named build/venv/target/... must not have
    # its whole tree excluded (source_files=0 -> coverage_pct=100.0 false green). Only dirs BELOW
    # the scan root count as excluded.
    proj = tmp_path / "build" / "proj"  # ancestor 'build' is an _EXCLUDED_DIR_PART
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "build").mkdir()  # an INNER build/ that SHOULD still be excluded
    (proj / "build" / "generated.py").write_text("y = 1\n", encoding="utf-8")
    payload = build_docs_coverage(str(proj))
    assert payload["totals"]["source_files"] == 1  # real.py counted, inner build/ excluded
    assert "src/real.py" in payload["uncovered_files"]
    assert "build/generated.py" not in payload["uncovered_files"]
    assert payload["totals"]["coverage_pct"] != 100.0  # was a false 100% before the fix


def test_stale_exclusion_matches_relative_parts(tmp_path):
    proj = tmp_path / "target" / "proj"  # ancestor 'target' is an _EXCLUDED_DIR_PART
    proj.mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "src" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("See `src/gone.py`.\n", encoding="utf-8")
    payload = build_docs_stale_references(str(proj))
    assert payload["totals"]["doc_files"] == 1  # doc under a 'target' ancestor is still scanned
    assert any(s["reference"] == "src/gone.py" for s in payload["stale_references"])


def test_fix_emits_paste_ready_markdown_table(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "bar.py").write_text(
        "\ndef undocumented_thing():  # a | pipe to escape\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("The main module is foo.py.\n", encoding="utf-8")

    payload = build_docs_coverage(str(tmp_path), include_details=True)
    details = payload["uncovered_details"]
    assert [d["path"] for d in details] == ["src/bar.py"]
    assert details[0]["size_bytes"] > 0
    # first NON-BLANK line, not the leading blank line
    assert details[0]["first_line"].startswith("def undocumented_thing()")

    table = render_docs_coverage_fix_markdown(payload)
    assert "| File | Size | First line |" in table
    assert "`src/bar.py`" in table
    assert "\\|" in table  # the pipe inside the first line is escaped so the table stays valid
    assert "src/foo.py" not in table  # covered files are not in the fix table


def test_details_absent_unless_requested(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bar.py").write_text("y = 2\n", encoding="utf-8")
    assert "uncovered_details" not in build_docs_coverage(str(tmp_path))


def test_ignore_glob_excludes_stub_group_entirely(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "a.stub.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.stub.py").write_text("x = 1\n", encoding="utf-8")
    # no governing doc -> everything is uncovered by default
    base = build_docs_coverage(str(tmp_path))
    assert set(base["uncovered_files"]) == {"src/a.stub.py", "src/b.stub.py", "src/real.py"}
    # --ignore drops the stub group: not flagged, and not counted (coverage_pct reflects real source)
    filtered = build_docs_coverage(str(tmp_path), ignore=("*.stub.py",))
    assert filtered["uncovered_files"] == ["src/real.py"]
    assert filtered["totals"]["source_files"] == 1
    assert filtered["applied_ignore"] == ["*.stub.py"]


def test_ignore_matches_relative_path_glob(tmp_path):
    (tmp_path / "commands" / "a").mkdir(parents=True)
    (tmp_path / "commands" / "a" / "index.js").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.js").write_text("y = 2\n", encoding="utf-8")
    filtered = build_docs_coverage(str(tmp_path), ignore=("commands/*/index.js",))
    assert "commands/a/index.js" not in filtered["uncovered_files"]
    assert "src/real.js" in filtered["uncovered_files"]


def test_uncovered_source_file_flagged(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "bar.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("The main module is foo.py.\n", encoding="utf-8")
    payload = build_docs_coverage(str(tmp_path))
    assert "src/bar.py" in payload["uncovered_files"]
    assert "src/foo.py" not in payload["uncovered_files"]  # covered by basename mention
    assert payload["totals"]["source_files"] == 2
    assert payload["totals"]["uncovered"] == 1
    assert payload["totals"]["doc_files"] == 1


def test_covered_by_relative_path(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("See src/foo.py for details.\n", encoding="utf-8")
    payload = build_docs_coverage(str(tmp_path))
    assert payload["uncovered_files"] == []
    assert payload["totals"]["coverage_pct"] == 100.0


def test_docs_and_non_source_excluded(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("nothing relevant\n", encoding="utf-8")  # a doc, not source
    (tmp_path / "data.json").write_text("{}\n", encoding="utf-8")  # not a source suffix
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    payload = build_docs_coverage(str(tmp_path))
    assert payload["totals"]["source_files"] == 1  # only a.py is a source file
    assert "a.py" in payload["uncovered_files"]  # CLAUDE.md never mentions it
    assert payload["totals"]["doc_files"] == 1


def test_missing_path_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_docs_coverage(str(tmp_path / "does-not-exist"))


def test_render_text_is_ascii_and_lists_uncovered(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("empty\n", encoding="utf-8")
    text = render_docs_coverage_text(build_docs_coverage(str(tmp_path)))
    assert text.isascii()  # cp1252-safe (no emoji/arrows)
    assert "a.py" in text
    assert "Undocumented source files" in text


# ==================================================================================================
# CEO v1.72.1 dogfood M1: docs-coverage was missing --deadline entirely (repo-scanning command like
# its siblings). Mirrors test_repo_map_deadline.py's "already expired" idiom -- an ALREADY-PAST
# absolute deadline (deadline_seconds=-1.0 -> time.monotonic() - 1.0) deterministically trips the
# walk's own deadline check on its very first iteration, with zero wall-clock racing (no dependency
# on machine speed / repo size, unlike a real --deadline 0.1 CLI invocation against this walk-only,
# much-cheaper-per-file scan -- see test_cli_deadline_flag.py for the CLI-layer wiring/gate tests).
# ==================================================================================================


def test_deadline_already_expired_returns_partial_immediately(tmp_path):
    (tmp_path / "src").mkdir()
    for index in range(6):
        (tmp_path / "src" / f"m{index}.py").write_text(f"x = {index}\n", encoding="utf-8")
    result = build_docs_coverage(str(tmp_path), deadline_seconds=-1.0)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_deadline_none_is_no_op(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = build_docs_coverage(str(tmp_path))
    assert "partial" not in result  # golden-parity: no deadline -> unchanged shape
    assert "deadline_limit" not in result


def test_stale_deadline_already_expired_returns_partial_immediately(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("See `src/gone.py`.\n", encoding="utf-8")
    result = build_docs_stale_references(str(tmp_path), deadline_seconds=-1.0)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_stale_deadline_none_is_no_op(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("no references here\n", encoding="utf-8")
    result = build_docs_stale_references(str(tmp_path))
    assert "partial" not in result
    assert "deadline_limit" not in result


def test_cli_accepts_deadline_flag(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["docs-coverage", str(tmp_path), "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
