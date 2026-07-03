"""TDD suite for ``tg inventory`` (round-4 [e], design-council test list)."""

import json

import pytest

from tensor_grep.cli.inventory import build_inventory


def _write(root, rel, content=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


class TestFailClosedAndBasics:
    def test_empty_repo_returns_zero_totals_not_error(self, tmp_path):
        inv = build_inventory(str(tmp_path))
        assert inv["totals"] == {"files": 0, "bytes": 0}
        assert inv["languages"] == []
        assert inv["scan_limit"]["possibly_truncated"] is False

    def test_nonexistent_path_fails_closed_not_empty_inventory(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError):
            build_inventory(str(missing))

    def test_single_file_path_returns_one_file_inventory(self, tmp_path):
        f = _write(tmp_path, "solo.py", "print(1)\n")
        inv = build_inventory(str(f))
        assert inv["totals"]["files"] == 1
        assert inv["top_level_dirs"] == []
        assert any(rec["language"] == "python" for rec in inv["languages"])


class TestWalkExclusions:
    def test_tensor_grep_index_dir_excluded_from_counts(self, tmp_path):
        _write(tmp_path, "real.py", "a=1\n")
        _write(tmp_path, ".tensor-grep/index.db", "SHOULD NOT COUNT")
        _write(tmp_path, ".git/config", "[core]")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 1  # only real.py

    def test_no_gitignore_present_walks_full_repo(self, tmp_path):
        _write(tmp_path, "a.py")
        _write(tmp_path, "src/b.py")
        _write(tmp_path, "docs/c.md")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 3

    def test_symlink_loop_does_not_hang_or_double_count(self, tmp_path):
        _write(tmp_path, "real.py", "a=1\n")
        loop = tmp_path / "loop"
        try:
            loop.symlink_to(tmp_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        inv = build_inventory(str(tmp_path))
        # symlinks are is_dir(follow_symlinks=False)==False -> skipped; real.py counted once.
        assert inv["totals"]["files"] == 1


class TestBinaryHandling:
    def test_binary_file_counted_separately_not_misclassified_as_language(self, tmp_path):
        _write(tmp_path, "blob.py", b"\x00\x01\x02binary")  # .py extension but binary content
        _write(tmp_path, "code.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        assert inv["binary"]["files"] == 1
        python = next((r for r in inv["languages"] if r["language"] == "python"), None)
        assert python is not None and python["files"] == 1  # only the real .py, not the blob

    def test_binary_file_bytes_included_in_total_but_flagged(self, tmp_path):
        _write(tmp_path, "blob.bin", b"\x00" * 100)
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 1
        assert inv["totals"]["bytes"] == 100
        assert inv["binary"] == {"files": 1, "bytes": 100}
        assert inv["languages"] == []  # binary not classified as a language


class TestClassificationEdges:
    def test_no_extension_file_counted_in_totals_via_basename_fallback(self, tmp_path):
        _write(tmp_path, "Makefile", "all:\n")
        _write(tmp_path, "Dockerfile", "FROM x\n")
        _write(tmp_path, "LICENSE", "MIT\n")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 3
        labels = {rec["language"] for rec in inv["languages"]}
        assert {"make", "dockerfile", "text"} <= labels

    def test_dotfile_counted_not_silently_excluded(self, tmp_path):
        _write(tmp_path, ".env", "KEY=1\n")
        _write(tmp_path, "app.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        assert inv["totals"]["files"] == 2  # .env is a file, not silently dropped

    def test_test_file_categorized_as_test_not_code(self, tmp_path):
        _write(tmp_path, "tests/test_thing.py", "def test_x():\n    pass\n")
        _write(tmp_path, "src/thing.py", "x=1\n")
        inv = build_inventory(str(tmp_path))
        cats = {rec["category"]: rec["files"] for rec in inv["categories"]}
        assert cats.get("test") == 1
        assert cats.get("code") == 1


class TestTruncationHonesty:
    def test_truncation_over_max_files_sets_possibly_truncated_and_cause(self, tmp_path):
        for i in range(5):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        inv = build_inventory(str(tmp_path), max_files=3)
        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "project-files"
        assert inv["scan_limit"]["scanned_files"] == 3
        assert inv["totals"]["files"] == 3  # counts are a floor

    def test_no_cap_hit_sets_truncation_cause_none(self, tmp_path):
        for i in range(3):
            _write(tmp_path, f"f{i}.py", "x=1\n")
        inv = build_inventory(str(tmp_path), max_files=100)
        assert inv["scan_limit"]["possibly_truncated"] is False
        assert inv["scan_limit"]["truncation_cause"] is None


class TestDeterminism:
    def test_languages_sorted_bytes_desc_then_name(self, tmp_path):
        _write(tmp_path, "big.py", "x" * 1000)
        _write(tmp_path, "small.rs", "y" * 10)
        _write(tmp_path, "mid.go", "z" * 100)
        inv = build_inventory(str(tmp_path))
        langs = [rec["language"] for rec in inv["languages"]]
        assert langs == ["python", "go", "rust"]  # bytes desc

    def test_top_level_dirs_sorted_lexicographically(self, tmp_path):
        _write(tmp_path, "zeta/a.py")
        _write(tmp_path, "alpha/b.py")
        _write(tmp_path, "mid/c.py")
        inv = build_inventory(str(tmp_path))
        dirs = [rec["dir"] for rec in inv["top_level_dirs"]]
        assert dirs == ["alpha", "mid", "zeta"]

    def test_json_output_stable_byte_identical_on_repeat_run(self, tmp_path):
        _write(tmp_path, "a.py", "x=1\n")
        _write(tmp_path, "src/b.rs", "fn main(){}\n")
        _write(tmp_path, "README.md", "# hi\n")
        first = json.dumps(build_inventory(str(tmp_path)), sort_keys=False)
        second = json.dumps(build_inventory(str(tmp_path)), sort_keys=False)
        assert first == second


class TestRegistration:
    def test_cli_default_max_files_matches_module_constant(self):
        import inspect

        from tensor_grep.cli import main as tg_main
        from tensor_grep.cli.inventory import DEFAULT_MAX_INVENTORY_FILES

        default = inspect.signature(tg_main.inventory).parameters["max_repo_files"].default
        # typer.Option returns an OptionInfo whose .default holds the literal value.
        assert default.default == DEFAULT_MAX_INVENTORY_FILES

    def test_inventory_in_known_commands_and_parity_set(self):
        from tensor_grep.cli.commands import KNOWN_COMMANDS

        assert "inventory" in KNOWN_COMMANDS
