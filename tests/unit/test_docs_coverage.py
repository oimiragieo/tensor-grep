"""tg docs-coverage: source files not referenced by any governing doc (CLAUDE.md/README/AGENTS.md).

From v1.19.9 dogfood -- the reporter's "most valuable thing in this whole sweep." Reference-existence
only (not semantic), lenient path-or-basename match so it under-reports gaps rather than flooding.
"""

import pytest

from tensor_grep.cli.docs_coverage import (
    build_docs_coverage,
    render_docs_coverage_fix_markdown,
    render_docs_coverage_text,
)


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
