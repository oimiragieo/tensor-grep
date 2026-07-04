"""
Regression tests for L2: possibly_truncated false-alarm on vendor/cache dirs.

Verified bugs fixed:
- .tmp-ci* dirs (hyphen prefix) were not excluded from the file walk
- 'vendor', 'site-packages', 'pods', 'gems' were not in _SKIP_DIR_NAMES
- possibly_truncated was set True even when only vendor dirs filled the cap
- truncation_cause field was missing

These tests use import-light calls against public API functions so they run
fast without needing the daemon or Rust core.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)


def _write_files(parent: Path, names: list[str], content: str = "x = 1\n") -> None:
    parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        (parent / name).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit-level tests for _should_skip_repo_dir
# ---------------------------------------------------------------------------


def test_should_skip_tmp_hyphen_prefix() -> None:
    """_should_skip_repo_dir must skip dirs starting with .tmp- (not just .tmp_)."""
    from tensor_grep.cli.repo_map import _should_skip_repo_dir

    assert _should_skip_repo_dir(Path("/repo/.tmp-ci")), ".tmp-ci should be skipped"
    assert _should_skip_repo_dir(Path("/repo/.tmp-ci-123")), ".tmp-ci-123 should be skipped"
    assert _should_skip_repo_dir(Path("/repo/.tmp_foo")), ".tmp_foo should be skipped"
    # sanity: real dirs should NOT be skipped
    assert not _should_skip_repo_dir(Path("/repo/src")), "src should not be skipped"
    assert not _should_skip_repo_dir(Path("/repo/myapp")), "myapp should not be skipped"


def test_skip_dir_names_includes_vendor_dirs() -> None:
    """vendor, site-packages, pods, gems must all be in _SKIP_DIR_NAMES."""
    from tensor_grep.cli.repo_map import _SKIP_DIR_NAMES

    for name in ("vendor", "site-packages", "pods", "gems", ".bundle", ".gradle", ".cargo"):
        assert name in _SKIP_DIR_NAMES, f"'{name}' should be in _SKIP_DIR_NAMES"


# ---------------------------------------------------------------------------
# Integration-level tests for build_repo_map scan_limit output
# ---------------------------------------------------------------------------


def test_tmp_ci_dir_excluded_from_walk() -> None:
    """Files inside .tmp-ci-* dirs must not appear in the repo map at all."""
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        # Real project file
        _write_files(root, ["main.py"], "def main(): pass\n")

        # Vendor dir that should be excluded
        _write_files(root / ".tmp-ci-vendor", [f"f{i}.py" for i in range(20)])

        result = build_repo_map(str(root))
        files = result.get("files", []) + result.get("tests", [])
        vendor_files = [f for f in files if ".tmp-ci" in f]
        assert vendor_files == [], f"Vendor files leaked into map: {vendor_files}"
        # Real project file should still appear
        assert any("main.py" in f for f in files), "main.py should be in the map"


def test_vendor_dir_excluded_from_walk() -> None:
    """Files inside vendor/ must not appear in the repo map."""
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        _write_files(root, ["main.py"], "def main(): pass\n")
        _write_files(root / "vendor", [f"dep_{i}.py" for i in range(30)])

        result = build_repo_map(str(root))
        files = result.get("files", []) + result.get("tests", [])
        vendor_files = [f for f in files if "vendor" in Path(f).parts]
        assert vendor_files == [], f"vendor/ files leaked into map: {vendor_files}"


def test_possibly_truncated_false_when_only_vendor_would_exceed_cap() -> None:
    """
    When the file cap is large enough for project files but would have been
    exhausted by .tmp-ci files (now excluded), possibly_truncated must be False.
    """
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        # 3 real project files
        _write_files(root, ["a.py", "b.py", "c.py"])

        # 600 vendor files that would flood a cap of 512 if not excluded
        _write_files(root / ".tmp-ci-cache", [f"ci_{i}.py" for i in range(600)])

        # Use a cap well above 3 but below 603
        result = build_repo_map(str(root), max_repo_files=512)
        sl = result.get("scan_limit", {})

        assert sl.get("scanned_files", 0) <= 3, (
            f"scanned_files should be <=3 (vendor excluded), got {sl.get('scanned_files')}"
        )
        assert sl.get("possibly_truncated") is False, (
            f"possibly_truncated should be False (vendor excluded), got {sl.get('possibly_truncated')}"
        )


def test_truncation_cause_project_files_when_real_files_dropped() -> None:
    """
    When the cap is hit because there are more project files than max_repo_files,
    truncation_cause must be 'project-files' and possibly_truncated must be True.
    """
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        src = root / "src"
        src.mkdir()
        for i in range(20):
            (src / f"module_{i}.py").write_text(f"def func_{i}(): pass\n", encoding="utf-8")

        result = build_repo_map(str(root), max_repo_files=5)
        sl = result.get("scan_limit", {})

        assert sl.get("possibly_truncated") is True, (
            f"possibly_truncated should be True, got {sl.get('possibly_truncated')}"
        )
        assert sl.get("truncation_cause") == "project-files", (
            f"truncation_cause should be 'project-files', got {sl.get('truncation_cause')}"
        )


def test_scan_limit_remediation_present_only_when_truncated() -> None:
    """dogfood 1.28.3 feature #3: a truncated scan carries a machine-readable
    scan_limit.remediation so a JSON-consuming agent gets the actionable next step without parsing
    the stderr warning (a truncated zero/small count otherwise reads as a real answer). A complete
    scan carries remediation=None."""
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)
        src = root / "src"
        src.mkdir()
        for i in range(20):
            (src / f"module_{i}.py").write_text(f"def func_{i}(): pass\n", encoding="utf-8")

        truncated = build_repo_map(str(root), max_repo_files=5).get("scan_limit", {})
        assert truncated.get("possibly_truncated") is True
        assert isinstance(truncated.get("remediation"), str) and truncated["remediation"], (
            "a truncated scan must carry a non-empty remediation string"
        )

        complete = build_repo_map(str(root), max_repo_files=512).get("scan_limit", {})
        assert complete.get("possibly_truncated") is False
        assert complete.get("remediation") is None


def test_truncation_cause_none_when_cap_not_reached() -> None:
    """truncation_cause should be None when cap is not reached."""
    from tensor_grep.cli.repo_map import build_repo_map

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        _write_files(root, ["a.py", "b.py"])

        result = build_repo_map(str(root), max_repo_files=512)
        sl = result.get("scan_limit", {})

        assert sl.get("possibly_truncated") is False
        assert sl.get("truncation_cause") is None


def test_scan_limit_json_output_has_truncation_cause() -> None:
    """build_repo_map_json must include truncation_cause in scan_limit."""
    from tensor_grep.cli.repo_map import build_repo_map_json

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_git_repo(root)

        src = root / "src"
        src.mkdir()
        for i in range(10):
            (src / f"file_{i}.py").write_text(f"x = {i}\n", encoding="utf-8")

        raw = build_repo_map_json(str(root), max_repo_files=3)
        obj = json.loads(raw)
        sl = obj.get("scan_limit", {})

        assert "truncation_cause" in sl, "truncation_cause must be present in scan_limit JSON"
        assert sl["truncation_cause"] == "project-files"
        assert sl["possibly_truncated"] is True


def test_path_has_vendor_component_helper() -> None:
    """_path_has_vendor_component correctly identifies vendor paths."""
    from tensor_grep.cli.repo_map import _path_has_vendor_component

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # vendor path
        vendor_file = root / "vendor" / "dep.py"
        vendor_file.parent.mkdir(parents=True, exist_ok=True)
        vendor_file.touch()
        assert _path_has_vendor_component(vendor_file, root), "vendor/dep.py should be vendor"

        # .tmp-ci path
        ci_file = root / ".tmp-ci-test" / "f.py"
        ci_file.parent.mkdir(parents=True, exist_ok=True)
        ci_file.touch()
        assert _path_has_vendor_component(ci_file, root), ".tmp-ci-test/f.py should be vendor"

        # real project file
        src_file = root / "src" / "main.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.touch()
        assert not _path_has_vendor_component(src_file, root), "src/main.py should NOT be vendor"

        # node_modules
        nm_file = root / "node_modules" / "pkg" / "index.js"
        nm_file.parent.mkdir(parents=True, exist_ok=True)
        nm_file.touch()
        assert _path_has_vendor_component(nm_file, root), "node_modules/... should be vendor"
