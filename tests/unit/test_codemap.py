"""Tests for build_codemap -- the persisted, browsable folder->file->symbol code map (`tg
codemap`). Fixture: tests/fixtures/codemap_repo/ (copied fresh into tmp_path per test so
generated output never touches the checked-in fixture and mtimes are stable within a run).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tensor_grep.cli import codemap as _codemap
from tensor_grep.cli.codemap import build_codemap, build_codemap_json

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "codemap_repo"

_FIXED_REVISION = {
    "status": "present",
    "commit_sha": "abc123def456abc123def456abc123def456ab",
    "branch": "main",
    "dirty": False,
    "dirty_tree_sha256": "e" * 64,
    "dirty_file_count": 0,
}


def _fixed_revision_identity(_root: Path) -> dict:
    return dict(_FIXED_REVISION)


def _fixed_now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _copy_fixture(dest_parent: Path) -> Path:
    dest = dest_parent / "repo"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_git_repo(repo: Path) -> None:
    _run_git(["init", "-b", "main", "."], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Test User"], cwd=repo)
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-m", "initial commit"], cwd=repo)


def _build(repo: Path, **kwargs):
    kwargs.setdefault("_revision_identity", _fixed_revision_identity)
    kwargs.setdefault("_now", _fixed_now)
    return build_codemap(repo, **kwargs)


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# (1) index lists only folders w/ mapped files + every row's page exists
# ---------------------------------------------------------------------------


def test_index_lists_only_folders_with_mapped_files_and_pages_exist(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)

    index_text = Path(payload["index"]).read_text(encoding="utf-8")

    # assets/ contains only an unmapped .bin extension -> must NOT appear as a folder row.
    assert "`assets`" not in index_text
    assert payload.get("folders_with_no_mapped_files", 0) >= 1

    for folder_display, expected_slug in [
        (".", "_root"),
        ("pkg", "pkg"),
        ("pkg/sub", "pkg_sub"),
        ("native", "native"),
    ]:
        assert f"`{folder_display}`" in index_text, f"missing folder row for {folder_display!r}"
        page_path = Path(payload["out"]) / f"{expected_slug}.md"
        assert page_path.is_file(), f"page for {folder_display!r} was not written: {page_path}"


# ---------------------------------------------------------------------------
# (2) folder page has file purpose + signatures + docstrings + Class.method label
# ---------------------------------------------------------------------------


def test_folder_page_has_purpose_signature_docstring_and_class_method_label(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")

    assert "### core.py" in pkg_page
    assert "Purpose: Core module docstring sentence." in pkg_page
    # the SECOND sentence of a docstring must never leak into the first-sentence excerpt.
    assert "must drop" not in pkg_page
    assert "must not appear" not in pkg_page

    # Class.method attribution: the method must be qualified, never the bare unqualified form.
    assert "`def render(self, size)`" not in pkg_page
    assert "`def Widget.render(self, size)`" in pkg_page
    assert "Render the widget at the given size." in pkg_page

    assert "`class Widget`" in pkg_page
    assert "`def bare_documented()`" in pkg_page
    assert "A bare module-level function with a docstring." in pkg_page

    # Folder blurb fallback chain: pkg/ has no README.md of its own -> falls back to
    # __init__.py's module docstring first sentence.
    assert "Package pkg: a small demo package for codemap tests." in pkg_page

    root_page = (Path(payload["out"]) / "_root.md").read_text(encoding="utf-8")
    # Root folder blurb: README.md exists at repo root -> its first prose sentence.
    assert "tiny fixture repository" in root_page


def test_typescript_and_rust_use_source_line_signature_fallback(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)

    sub_page = (Path(payload["out"]) / "pkg_sub.md").read_text(encoding="utf-8")
    assert "### util.ts" in sub_page
    assert "Language: TypeScript" in sub_page
    assert "export function formatLabel(value: string): string {" in sub_page
    assert "export class Formatter {" in sub_page

    native_page = (Path(payload["out"]) / "native.md").read_text(encoding="utf-8")
    assert "### lib.rs" in native_page
    assert "Language: Rust" in native_page
    assert "pub fn parse(input: &str) -> usize {" in native_page
    assert "Parses the input and returns its length." in native_page


# ---------------------------------------------------------------------------
# (3) symbol cap emits overflow pointer
# ---------------------------------------------------------------------------


def test_symbol_cap_emits_overflow_pointer(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, max_symbols_per_file=10)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "... +50 more (run: tg defs <name> pkg/many_symbols.py)" in pkg_page


def test_default_symbol_cap_does_not_overflow_a_small_file(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)  # default max_symbols_per_file=50; many_symbols.py has 60 defs

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "more (run: tg defs <name> pkg/many_symbols.py)" in pkg_page
    assert "more (run: tg defs <name> pkg/core.py)" not in pkg_page  # only 4 symbols, no overflow


# ---------------------------------------------------------------------------
# (4) undocumented symbol -> EMPTY description (assert the _infer_from_name filler ABSENT)
# ---------------------------------------------------------------------------


def test_undocumented_symbol_has_empty_description_no_name_echo_filler(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    for line in pkg_page.splitlines():
        if "bare_undocumented" in line and line.strip().startswith("|"):
            cells = _table_cells(line)
            assert cells[-1] == "", f"expected an EMPTY description cell, got: {line!r}"
            break
    else:
        pytest.fail("bare_undocumented row not found in pkg.md")


# ---------------------------------------------------------------------------
# (5) --out/--index excluded from universe (2nd run identical)
# ---------------------------------------------------------------------------


def test_second_run_does_not_absorb_its_own_output(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    first = _build(repo)
    second = _build(repo)

    assert first["files_total"] == second["files_total"], (
        "a second run must not pick up the first run's own generated pages as new source files"
    )
    assert first["folders_total"] == second["folders_total"]
    assert first["tree_manifest_sha256"] == second["tree_manifest_sha256"]

    index_text = Path(second["index"]).read_text(encoding="utf-8")
    assert "`docs`" not in index_text  # the output tree itself must never become a folder row

    # _coverage.json/index.md/*.md must never appear as a RENDERED FILE ENTRY (a `### name`
    # heading) in any folder page -- the exclusions-section prose is allowed to name
    # "_coverage.json" as documentation, so this checks the actual per-file headings instead of a
    # blanket substring search over the whole index.
    for written in second["written_files"]:
        if not written.endswith(".md") or written == second["index"]:
            continue
        page_text = Path(written).read_text(encoding="utf-8")
        assert "### _coverage.json" not in page_text
        assert "### index.md" not in page_text


def test_custom_index_filename_resolves_inside_out_and_is_excluded(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, index="claude-index.md")

    assert Path(payload["index"]).name == "claude-index.md"
    assert Path(payload["index"]).parent == Path(payload["out"])
    assert Path(payload["index"]).is_file()

    # Re-running must still be stable (the custom index name is excluded too).
    second = _build(repo, index="claude-index.md")
    assert payload["files_total"] == second["files_total"]


# ---------------------------------------------------------------------------
# (6) deterministic (inject _revision_identity + _now -> byte-identical)
# ---------------------------------------------------------------------------


def test_byte_identical_output_across_two_runs_with_injected_clock_and_revision(
    tmp_path: Path,
) -> None:
    repo = _copy_fixture(tmp_path)

    first = _build(repo)
    index_text_1 = Path(first["index"]).read_text(encoding="utf-8")
    pkg_text_1 = (Path(first["out"]) / "pkg.md").read_text(encoding="utf-8")

    second = _build(repo)
    index_text_2 = Path(second["index"]).read_text(encoding="utf-8")
    pkg_text_2 = (Path(second["out"]) / "pkg.md").read_text(encoding="utf-8")

    assert index_text_1 == index_text_2, "index.md must be byte-identical across two runs"
    assert pkg_text_1 == pkg_text_2, "pkg.md must be byte-identical across two runs"
    assert first["tree_manifest_sha256"] == second["tree_manifest_sha256"]


# ---------------------------------------------------------------------------
# (7) stamp line in index AND every page
# ---------------------------------------------------------------------------


def test_stamp_line_present_in_index_and_every_folder_page(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo)

    stamp_fragment = "Verify: tg codemap --check"
    index_text = Path(payload["index"]).read_text(encoding="utf-8")
    assert stamp_fragment in index_text
    assert "abc123def456" in index_text  # sha12 of the injected fixed commit
    assert "(clean)" in index_text

    page_count = 0
    for written in payload["written_files"]:
        if written.endswith(".md") and written != payload["index"]:
            page_count += 1
            page_text = Path(written).read_text(encoding="utf-8")
            assert stamp_fragment in page_text, f"missing stamp line in {written}"
    assert page_count >= 3  # pkg, pkg/sub, native (at least) all got a page


# ---------------------------------------------------------------------------
# (8) stdout ASCII (non-ASCII path/docstring fixture) -- the ASCII rule is about THIS module's
# own truncation marker, never U+2026, even when formatting non-ASCII source content verbatim.
# ---------------------------------------------------------------------------


def test_truncate_ascii_never_emits_the_unicode_ellipsis() -> None:
    truncated = _codemap._truncate_ascii("x" * 300, 220)
    assert truncated.endswith("...")
    assert "…" not in truncated
    assert truncated.isascii()


def test_output_survives_non_ascii_docstring_and_stays_valid_utf8(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    weird_dir = repo / "weird"
    weird_dir.mkdir()
    (weird_dir / "mod.py").write_text(
        '"""Café docstring with an em dash — and 中文 text."""\n\n\ndef f():\n    return 1\n',
        encoding="utf-8",
    )

    payload = _build(repo)
    weird_page = Path(payload["out"]) / "weird.md"
    assert weird_page.is_file()
    text = weird_page.read_text(encoding="utf-8")
    # This module must never INJECT the U+2026 ellipsis itself (rule 6); it MAY legitimately
    # carry verbatim non-ASCII source content (generated files are UTF-8, not ASCII-only).
    assert "…" not in text
    assert "Café" in text

    coverage_path = Path(payload["out"]) / "_coverage.json"
    json.loads(coverage_path.read_text(encoding="utf-8"))  # must round-trip as valid JSON


# ---------------------------------------------------------------------------
# JSON output path
# ---------------------------------------------------------------------------


def test_json_output_is_parseable_and_matches_build_codemap(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    text = build_codemap_json(repo, _revision_identity=_fixed_revision_identity, _now=_fixed_now)
    parsed = json.loads(text)
    assert parsed["files_total"] > 0
    assert "written_files" in parsed
    assert parsed["revision"]["commit_sha"] == _FIXED_REVISION["commit_sha"]


# ---------------------------------------------------------------------------
# (14) tracked-only map universe: untracked/gitignored files never enter the payload
# ---------------------------------------------------------------------------


def test_untracked_source_file_is_excluded_from_map_payload(tmp_path: Path) -> None:
    """A stray untracked .py file (never `git add`ed) must not appear as a mapped file -- its
    volatile existence/mtime must never leak into the persisted, browsable inventory."""
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)

    (repo / "pkg" / "untracked_scratch.py").write_text(
        "def scratch():\n    return 1\n", encoding="utf-8"
    )

    payload = _build(repo)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "### untracked_scratch.py" not in pkg_page


def test_gitignored_source_file_is_excluded_from_map_payload(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    (repo / ".gitignore").write_text("pkg/ignored_scratch.py\n", encoding="utf-8")
    _init_git_repo(repo)

    (repo / "pkg" / "ignored_scratch.py").write_text(
        "def ignored():\n    return 1\n", encoding="utf-8"
    )

    payload = _build(repo)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "### ignored_scratch.py" not in pkg_page


def test_tracked_files_still_appear_in_map_payload_in_a_git_repo(tmp_path: Path) -> None:
    """The tracked-only filter must not become an accidental deny-all: every file that IS tracked
    keeps appearing exactly as before."""
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)

    payload = _build(repo)

    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "### core.py" in pkg_page


def test_non_git_repo_map_payload_degrades_gracefully_keeps_all_files(tmp_path: Path) -> None:
    """Outside a git repo, the tracked-only filter must degrade to a no-op (keep everything), not
    crash and not silently produce an empty map."""
    repo = _copy_fixture(tmp_path)  # never git-init'd

    payload = _build(repo)

    assert payload["files_total"] > 0
    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "### core.py" in pkg_page


# ---------------------------------------------------------------------------
# PATH contract: a file path must error, never silently scan the parent.
# ---------------------------------------------------------------------------


def test_file_path_raises_not_a_directory(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    a_file = repo / "README.md"

    with pytest.raises(NotADirectoryError):
        build_codemap(a_file)


def test_missing_path_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_codemap(tmp_path / "does-not-exist")
