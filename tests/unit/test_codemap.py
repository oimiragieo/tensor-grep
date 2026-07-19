"""Tests for build_codemap -- the persisted, browsable folder->file->symbol code map (`tg
codemap`). Fixture: tests/fixtures/codemap_repo/ (copied fresh into tmp_path per test so
generated output never touches the checked-in fixture and mtimes are stable within a run).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import codemap as _codemap
from tensor_grep.cli.codemap import build_codemap, build_codemap_json
from tensor_grep.cli.main import app

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
# (15) --ignore excludes matching files/folders from the generated pages + index (mirrors
# orient_capsule._apply_ignore_globs, the same repeatable glob-exclude helper `tg orient`/`tg
# agent` already use).
# ---------------------------------------------------------------------------


def test_ignore_glob_excludes_matching_file_from_pages_and_index(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, ignore=("native/*",))

    index_text = Path(payload["index"]).read_text(encoding="utf-8")
    assert "`native`" not in index_text
    assert not (Path(payload["out"]) / "native.md").exists()

    # A file untouched by the glob must still be mapped exactly as before.
    pkg_page = (Path(payload["out"]) / "pkg.md").read_text(encoding="utf-8")
    assert "### core.py" in pkg_page


def test_repeatable_ignore_globs_both_apply(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, ignore=("native/*", "pkg/sub/*"))

    index_text = Path(payload["index"]).read_text(encoding="utf-8")
    assert "`native`" not in index_text
    assert "`pkg/sub`" not in index_text
    assert "`pkg`" in index_text  # pkg/core.py etc. is untouched by either glob

    assert not (Path(payload["out"]) / "native.md").exists()
    assert not (Path(payload["out"]) / "pkg_sub.md").exists()
    assert (Path(payload["out"]) / "pkg.md").is_file()


# ---------------------------------------------------------------------------
# (16) --deadline bounds the scan: an expired budget yields partial:true + partial_reason
# "deadline" and STILL returns a valid, usable result (no hang, no crash); a generous budget
# yields partial:false. Deterministic via the relative-seconds form of
# test_repo_map_deadline.py's already-expired-deadline idiom (deadline_monotonic =
# time.monotonic() - 1.0) -- never a real wall-clock race (anti-hang-test-protocol).
# ---------------------------------------------------------------------------


def test_expired_deadline_yields_partial_true_with_deadline_reason(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, deadline_seconds=-1.0)

    assert payload["partial"] is True
    assert payload["partial_reason"] == "deadline"
    assert payload["remediation"]
    # Still a VALID, usable result -- build_codemap must not hang or raise.
    assert isinstance(payload["files_total"], int)
    assert Path(payload["index"]).is_file()
    coverage_path = Path(payload["out"]) / _codemap._COVERAGE_FILENAME
    json.loads(coverage_path.read_text(encoding="utf-8"))  # persisted coverage stays valid JSON


def test_generous_deadline_yields_partial_false(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = _build(repo, deadline_seconds=120.0)

    assert payload["partial"] is False
    assert payload["partial_reason"] is None


# ---------------------------------------------------------------------------
# #153: the `tg codemap` CLI front door defaults --deadline to a bounded value (was None/unbounded,
# which could hang ~90s on a huge multi-root workspace) -- build_codemap's own library default
# stays None (see test_default_invocation_matches_explicit_noop_ignore_and_deadline above, which
# must stay untouched). These are the first CliRunner-based tests in this file: they spy on
# codemap.build_codemap (monkeypatched on the module, picked up by main.py's lazy
# `from tensor_grep.cli.codemap import build_codemap` at call time -- the same pattern
# tests/unit/test_cli_deadline_flag.py uses for repo_map.build_symbol_callers) so no real repo scan
# runs.
# ---------------------------------------------------------------------------


def _stub_cli_codemap_payload(path: str | Path) -> dict:
    out_dir = Path(path) / "docs" / "code-map"
    return {
        "path": str(path),
        "out": str(out_dir),
        "index": str(out_dir / "index.md"),
        "folders_total": 0,
        "files_total": 0,
        "symbols_total": 0,
        "partial": False,
        "partial_reason": None,
    }


def test_codemap_cli_defaults_to_bounded_deadline(tmp_path: Path, monkeypatch) -> None:
    recorded: dict = {}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_cli_codemap_payload(path)

    monkeypatch.setattr(_codemap, "build_codemap", _spy)
    result = CliRunner().invoke(app, ["codemap", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 60.0


def test_codemap_cli_no_deadline_flag_passes_none(tmp_path: Path, monkeypatch) -> None:
    recorded: dict = {"deadline_seconds": "sentinel-untouched"}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_cli_codemap_payload(path)

    monkeypatch.setattr(_codemap, "build_codemap", _spy)
    result = CliRunner().invoke(app, ["codemap", str(tmp_path), "--no-deadline"])

    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") is None


def test_codemap_cli_explicit_deadline_still_overrides_default(tmp_path: Path, monkeypatch) -> None:
    recorded: dict = {}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_cli_codemap_payload(path)

    monkeypatch.setattr(_codemap, "build_codemap", _spy)
    result = CliRunner().invoke(app, ["codemap", str(tmp_path), "--deadline", "30"])

    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 30.0


def test_default_cli_deadline_literal_pins_main_py_option(tmp_path: Path, monkeypatch) -> None:
    """Guard test: main.py's --deadline typer.Option hardcodes a literal 60.0 (to keep the heavy
    codemap import lazy -- the same pattern DEFAULT_MAX_REPO_FILES/50_000 uses). Nothing else pins
    that literal against codemap.DEFAULT_CLI_DEADLINE_SECONDS, so the two could silently drift.
    (A comment near main.py's max_repo_files option claims an analogous guard test already exists
    for the 50_000 literal -- it doesn't; grepping the test suite confirms no such test. This is a
    real guard, for the new constant.)"""
    assert _codemap.DEFAULT_CLI_DEADLINE_SECONDS == 60.0

    recorded: dict = {}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_cli_codemap_payload(path)

    monkeypatch.setattr(_codemap, "build_codemap", _spy)
    CliRunner().invoke(app, ["codemap", str(tmp_path)])

    # The CLI's actual (hardcoded-in-main.py) default must match the module constant -- not just
    # two independently-hardcoded 60.0s that happen to agree today.
    assert recorded.get("deadline_seconds") == _codemap.DEFAULT_CLI_DEADLINE_SECONDS


# ---------------------------------------------------------------------------
# (17) additive contract: omitting --ignore/--deadline (or passing their explicit no-op
# defaults) must remain byte-identical to today's output.
# ---------------------------------------------------------------------------


def test_default_invocation_matches_explicit_noop_ignore_and_deadline(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)

    default_payload = _build(repo)
    default_index = Path(default_payload["index"]).read_text(encoding="utf-8")
    default_pkg = (Path(default_payload["out"]) / "pkg.md").read_text(encoding="utf-8")

    explicit_payload = _build(repo, ignore=(), deadline_seconds=None)
    explicit_index = Path(explicit_payload["index"]).read_text(encoding="utf-8")
    explicit_pkg = (Path(explicit_payload["out"]) / "pkg.md").read_text(encoding="utf-8")

    assert default_index == explicit_index
    assert default_pkg == explicit_pkg
    assert default_payload["partial"] is False
    assert explicit_payload["partial"] is False
    assert default_payload["partial_reason"] is None
    assert explicit_payload["partial_reason"] is None
    assert default_payload["files_total"] == explicit_payload["files_total"]
    assert default_payload["tree_manifest_sha256"] == explicit_payload["tree_manifest_sha256"]


# ---------------------------------------------------------------------------
# (18) dogfood finding 1: the POST-MAP tail (the folders_with_no_mapped_files re-walk + the
# per-folder render loop) ran fully UNBOUNDED even after the MAP-level scan finished inside
# --deadline -- a real `tg codemap ROOT --deadline 3` ran ~28s. Both must now bound themselves off
# the SAME deadline_monotonic and fold an early break into the EXISTING partial/partial_reason=
# "deadline" contract (18a), and the render loop's break must never leave the INDEX PAGE
# dangling-linking to a folder page that was never written (18b).
# ---------------------------------------------------------------------------


def _make_many_folder_repo(root: Path, folder_count: int) -> Path:
    """``folder_count`` distinct folders, each holding one trivial file -- enough real per-folder
    render-loop iterations to prove a MID-loop deadline break, not just a pre-loop check."""
    project = root / "project"
    for index in range(folder_count):
        folder = project / f"pkg{index:04d}"
        folder.mkdir(parents=True)
        (folder / "mod.py").write_text(f"def f{index}():\n    return {index}\n", encoding="utf-8")
    return project


def test_all_folder_paths_bounds_walk_on_expired_deadline(tmp_path: Path) -> None:
    project = _make_many_folder_repo(tmp_path, 5)
    flag = _codemap._repo_map._DeadlineBreakFlag()

    folders = _codemap._all_folder_paths(
        project,
        max_repo_files=100,
        deadline_monotonic=time.monotonic() - 1.0,
        deadline_hit=flag,
    )

    assert folders == set()
    assert flag.hit is True


def test_all_folder_paths_deadline_none_is_unaffected(tmp_path: Path) -> None:
    project = _make_many_folder_repo(tmp_path, 5)

    folders = _codemap._all_folder_paths(project, max_repo_files=100)

    assert len(folders) == 5


def test_tail_render_loop_honors_deadline_mid_loop_and_marks_partial(
    tmp_path: Path, monkeypatch
) -> None:
    """council must-fix #4: the per-folder render loop must break on --deadline and fold that
    into the SAME partial/partial_reason=deadline contract the scan-level cutoff already uses --
    proven via a mid-loop break (some but not all folders rendered), not just a pre-loop check."""
    project = _make_many_folder_repo(tmp_path, 10)

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(_codemap.time, "monotonic", lambda: clock["t"])
    original_render = _codemap._render_folder_page

    def _advancing_render(*args, **kwargs):
        clock["t"] += 1.0
        return original_render(*args, **kwargs)

    monkeypatch.setattr(_codemap, "_render_folder_page", _advancing_render)

    payload = _build(project, deadline_seconds=5.5)

    assert payload["partial"] is True
    assert payload["partial_reason"] == "deadline"
    assert payload["remediation"]
    written_pages = [Path(p) for p in payload["written_files"] if Path(p).stem.startswith("pkg")]
    # A genuine MID-loop break: some but not all 10 folders got a rendered page.
    assert 0 < len(written_pages) < 10, (
        f"expected a partial render (some but not all of 10 folders), got {len(written_pages)}"
    )
    for page_path in written_pages:
        assert page_path.is_file()
    # The still-valid coverage JSON must agree with the rendered index page.
    coverage_path = Path(payload["out"]) / _codemap._COVERAGE_FILENAME
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    assert coverage["partial"] is True
    assert coverage["partial_reason"] == "deadline"


def test_tail_render_loop_index_never_dangling_links_a_folder_deadline_cut(
    tmp_path: Path, monkeypatch
) -> None:
    """18b: the index page must list (and link to) ONLY folders that actually got a page written
    -- a dangling link to an unwritten folder page would be a broken-navigation regression."""
    project = _make_many_folder_repo(tmp_path, 8)

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(_codemap.time, "monotonic", lambda: clock["t"])
    original_render = _codemap._render_folder_page

    def _advancing_render(*args, **kwargs):
        clock["t"] += 1.0
        return original_render(*args, **kwargs)

    monkeypatch.setattr(_codemap, "_render_folder_page", _advancing_render)

    payload = _build(project, deadline_seconds=3.5)

    assert payload["partial"] is True
    index_path = Path(payload["index"])
    lines = index_path.read_text(encoding="utf-8").splitlines()
    # Scope to the "## Folders" table specifically -- the EARLIER "## Top central files" table
    # also has `| \`pkg0000/mod.py\` | ... |`-shaped rows (a FILE path, no link at all), which a
    # bare `startswith("| \`pkg")` scan over the whole page would misidentify as folder rows.
    folders_header = lines.index("## Folders")
    row_count = 0
    for line in lines[folders_header:]:
        if not line.startswith("| `pkg"):
            continue
        row_count += 1
        match = re.search(r"\[[^\]]+\]\(([^)]+)\)", line)
        assert match, f"folder row missing a map link: {line}"
        linked_page = (index_path.parent / match.group(1)).resolve()
        assert linked_page.is_file(), f"index links to an unwritten page: {linked_page}"
    assert row_count > 0, "fixture produced no folder rows to check"
    assert row_count < 8, "expected a partial render (fewer rows than the 8 real folders)"


def test_tail_no_deadline_renders_every_folder_unaffected(tmp_path: Path) -> None:
    project = _make_many_folder_repo(tmp_path, 6)

    payload = _build(project)

    assert payload["partial"] is False
    assert payload["folders_total"] == 6
    written_pages = [p for p in payload["written_files"] if Path(p).stem.startswith("pkg")]
    assert len(written_pages) == 6


# ---------------------------------------------------------------------------
# (19) tg-codemap 90s-timeout root cause: the revision-identity (`git rev-parse` + `git status`)
# and tracked-file-exclusion (`git ls-files`) subprocess calls were bounded ONLY by
# TG_GIT_TIMEOUT_SECONDS (120s default PER CALL) -- a budget entirely decoupled from --deadline,
# and revision-identity ran BEFORE deadline_monotonic even existed. On a large/slow working tree
# a single `git status` can itself take tens of seconds, so a 60s --deadline could still observe
# 100s+ of wall time from git calls alone before build_repo_map's own (already deadline-bounded)
# walk got a chance to run. Deterministic via monkeypatched time.monotonic (anti-hang-test-
# protocol: never a real sleep) -- mirrors this file's own (18) advancing-clock idiom.
# ---------------------------------------------------------------------------


def test_tracked_file_set_deadline_none_preserves_default_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    """Byte-identity guard: deadline_monotonic=None (every pre-existing caller) must pass the
    exact same timeout_seconds to run_subprocess as before this fix."""
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)
    captured_timeouts: list[float] = []
    real_run_subprocess = _codemap.run_subprocess

    def _spy(*args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout_seconds"))
        return real_run_subprocess(*args, **kwargs)

    monkeypatch.setattr(_codemap, "run_subprocess", _spy)

    tracked = _codemap._tracked_file_set(repo)

    assert tracked is not None
    assert captured_timeouts
    assert all(t == _codemap.configured_git_timeout_seconds() for t in captured_timeouts)


def test_tracked_file_set_caps_timeout_to_remaining_deadline_budget(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)
    captured_timeouts: list[float] = []
    real_run_subprocess = _codemap.run_subprocess

    def _spy(*args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout_seconds"))
        return real_run_subprocess(*args, **kwargs)

    monkeypatch.setattr(_codemap, "run_subprocess", _spy)
    monkeypatch.setattr(_codemap.time, "monotonic", lambda: 1000.0)

    tracked = _codemap._tracked_file_set(repo, deadline_monotonic=1005.0)

    assert tracked is not None
    assert captured_timeouts
    assert all(t <= 5.0 for t in captured_timeouts), (
        f"git ls-files timeout {captured_timeouts} was not capped to the ~5s remaining budget"
    )


def test_tracked_file_set_skips_git_when_deadline_already_expired(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)
    call_count = 0
    real_run_subprocess = _codemap.run_subprocess

    def _counting(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_run_subprocess(*args, **kwargs)

    monkeypatch.setattr(_codemap, "run_subprocess", _counting)
    monkeypatch.setattr(_codemap.time, "monotonic", lambda: 1000.0)

    tracked = _codemap._tracked_file_set(repo, deadline_monotonic=999.0)

    assert call_count == 0, "an already-expired deadline must skip git entirely, not invoke it"
    assert tracked is None


def test_exclude_untracked_paths_forwards_deadline_monotonic_to_tracked_file_set(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict = {}

    def _spy_tracked_file_set(root, *, deadline_monotonic=None):
        captured["deadline_monotonic"] = deadline_monotonic
        return None  # degrade to no-op -- the pre-existing contract for ANY git-unavailable case

    monkeypatch.setattr(_codemap, "_tracked_file_set", _spy_tracked_file_set)
    rm = {"files": ["a.py"], "tests": [], "symbols": [], "imports": []}

    result = _codemap._exclude_untracked_paths(rm, root=tmp_path, deadline_monotonic=42.0)

    assert captured["deadline_monotonic"] == 42.0
    assert result == rm


def test_build_codemap_degrades_honestly_when_revision_identity_git_call_would_be_slow(
    tmp_path: Path, monkeypatch
) -> None:
    """THE end-to-end SLA/honesty proof. Simulates a very slow `git status` (the tg-codemap
    90s-timeout root cause) WITHOUT a real sleep: each `run_subprocess` call advances a shared
    FAKE `time.monotonic()` clock by 40s (mirrors this file's own (18) advancing-clock idiom,
    the strongest deterministic pattern for this class of bug per anti-hang-test-protocol).

    Real wall-clock is measured separately via `time.perf_counter()` (never patched), so the
    bound below is a genuine, unforgeable proof: however long the fake clock claims the git
    call "took", build_codemap must still return promptly with an HONEST partial result --
    never hang, never crash, never silently claim completeness.
    """
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)

    clock = {"t": 1000.0}
    monkeypatch.setattr(_codemap.time, "monotonic", lambda: clock["t"])

    real_codemap_run_subprocess = _codemap.run_subprocess
    real_evidence_run_subprocess = _codemap._evidence_receipt.run_subprocess

    def _slow_run_subprocess(*args, **kwargs):
        clock["t"] += 40.0  # simulate a very slow git call -- no real sleep
        return real_codemap_run_subprocess(*args, **kwargs)

    def _slow_evidence_run_subprocess(*args, **kwargs):
        clock["t"] += 40.0
        return real_evidence_run_subprocess(*args, **kwargs)

    monkeypatch.setattr(_codemap, "run_subprocess", _slow_run_subprocess)
    monkeypatch.setattr(_codemap._evidence_receipt, "run_subprocess", _slow_evidence_run_subprocess)

    deadline_seconds = 10.0
    started = time.perf_counter()
    payload = build_codemap(repo, deadline_seconds=deadline_seconds)
    real_elapsed = time.perf_counter() - started

    assert real_elapsed < deadline_seconds * 2, (
        f"build_codemap took {real_elapsed:.2f}s of REAL wall time against a "
        f"{deadline_seconds}s --deadline while git calls were simulated slow -- looks unbounded"
    )
    # Honest partial: the scan/tail machinery downstream shares the SAME deadline_monotonic, so
    # once the (simulated-slow) revision-identity call alone exhausts the budget, every later
    # phase correctly observes "already past deadline" via the pre-existing per-iteration checks.
    assert payload["partial"] is True
    assert payload["partial_reason"] == "deadline"
    assert payload["remediation"]
    # The stamp must degrade honestly too (revision identity was skipped, not fabricated).
    assert payload["revision"]["status"] == "unavailable"
    assert Path(payload["index"]).is_file()
    index_text = Path(payload["index"]).read_text(encoding="utf-8")
    assert "no-git (manifest-only)" in index_text
    assert "- Partial: yes" in index_text
    assert "- Remediation:" in index_text
    coverage_path = Path(payload["out"]) / _codemap._COVERAGE_FILENAME
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    assert coverage["partial"] is True
    assert coverage["partial_reason"] == "deadline"


def test_build_codemap_revision_identity_deadline_wiring_is_noop_with_no_deadline_pressure(
    tmp_path: Path,
) -> None:
    """Regression guard for the "no-deadline-pressure path unchanged" contract: a real git repo
    with an ample deadline must still produce a fully-present revision identity, byte-identical
    in shape to before this fix -- the new deadline_monotonic threading through build_codemap's
    revision-identity call must be a true no-op when the budget is generous."""
    repo = _copy_fixture(tmp_path)
    _init_git_repo(repo)

    payload = build_codemap(repo, deadline_seconds=120.0)

    assert payload["partial"] is False
    assert payload["revision"]["status"] == "present"
    assert payload["revision"]["commit_sha"]


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
