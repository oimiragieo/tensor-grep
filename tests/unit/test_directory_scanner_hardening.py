"""Round-5 Q14/Q15 hardening: DirectoryScanner traversal budget + .gitignore byte cap.

Q14: an unbounded directory walk on a pathological tree (deep/wide fanout) must STOP once a
defensive entry budget is exceeded, and must flag the truncation rather than silently dropping
the remainder of the tree.

Q15: reading `.gitignore` must be byte-capped so a giant file (crafted or accidental) cannot be
slurped into memory whole; anything beyond the cap is ignored and flagged, without crashing.
"""

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


class TestDirectoryScannerTraversalBudget:
    def test_should_stop_and_flag_truncation_when_scan_budget_exceeded(self, tmp_path):
        # 20 subdirectories each holding one file -> os.walk visits far more than a
        # deliberately tiny budget can absorb.
        for i in range(20):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / "file.py").write_text("x", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), max_scan_entries=5)
        files = list(scanner.walk(str(tmp_path)))

        # The walk must not silently drop -- it must both stop short AND flag truncation.
        assert len(files) < 20
        assert scanner.scan_truncated is True
        assert scanner.scan_truncation_cause == "max-scan-entries"

    def test_should_not_flag_truncation_when_budget_is_sufficient(self, tmp_path):
        for i in range(5):
            sub = tmp_path / f"d{i}"
            sub.mkdir()
            (sub / "file.py").write_text("x", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), max_scan_entries=10_000)
        files = list(scanner.walk(str(tmp_path)))

        assert len(files) == 5
        assert scanner.scan_truncated is False
        assert scanner.scan_truncation_cause is None


class TestDirectoryScannerGitignoreByteCap:
    def test_should_cap_oversized_gitignore_without_crash(self, tmp_path):
        # A .gitignore far larger than a small test cap; must not be read whole into memory.
        huge_contents = "*.bin\n" * 200_000
        (tmp_path / ".gitignore").write_text(huge_contents, encoding="utf-8")

        keep = tmp_path / "keep.py"
        keep.write_text("ok", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), gitignore_max_bytes=64)

        # Must not raise, and the legitimate file must still be returned (it is not a *.bin
        # pattern, and the oversized ignore file is only partially honored, never crashes).
        files = list(scanner.walk(str(tmp_path)))

        assert str(keep) in files
        assert scanner.gitignore_truncated is True

    def test_should_not_flag_truncation_for_small_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
        keep = tmp_path / "keep.py"
        keep.write_text("ok", encoding="utf-8")
        ignored = tmp_path / "debug.log"
        ignored.write_text("ok", encoding="utf-8")

        scanner = DirectoryScanner(SearchConfig(), gitignore_max_bytes=64)
        files = list(scanner.walk(str(tmp_path)))

        assert str(keep) in files
        assert str(ignored) not in files
        assert scanner.gitignore_truncated is False
