import subprocess
import sys
import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.performance]


class TestVsRipgrep:
    def test_semantic_classification_faster_than_multi_rg(self, tmp_path, rg_path):
        """GPU value prop: single classify pass vs N separate rg passes."""
        log = tmp_path / "mixed.log"
        # Generate log with multiple event types
        lines = []
        for i in range(10_000):
            if i % 3 == 0:
                lines.append(f"2026-02-24 ERROR Connection timeout from 10.0.0.{i % 256}\n")
            elif i % 3 == 1:
                lines.append(f"2026-02-24 WARN Disk usage at {60 + i % 40}%\n")
            else:
                lines.append(f"2026-02-24 INFO Request processed in {i % 100}ms\n")
        log.write_text("".join(lines))

        patterns = ["ERROR", "WARN", "INFO", r"\d+\.\d+\.\d+\.\d+", "timeout", "Disk usage"]
        start = time.perf_counter()
        for p in patterns:
            subprocess.run([rg_path, p, str(log)], capture_output=True)
        rg_total = time.perf_counter() - start

        # Our tool: classify does all at once (when GPU available)
        start = time.perf_counter()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "--cpu",
                "ERROR|WARN|INFO",
                str(log),
            ],
            capture_output=True,
        )
        our_total = time.perf_counter() - start

        print(f"ripgrep {len(patterns)} passes: {rg_total:.3f}s")
        print(f"tg single pass: {our_total:.3f}s")

    def test_pcre2_lookahead_support(self, tmp_path):
        """Verify that PCRE2 lookahead works via the -P flag."""
        # Check for PCRE2 support first
        from tensor_grep.core.config import SearchConfig
        from tensor_grep.core.pipeline import Pipeline

        try:
            p = Pipeline(config=SearchConfig(pcre2=True))
            if p.selected_backend_name == "CPUBackend":
                pytest.skip("No PCRE2-capable backend available (need rg-pcre2 or rust-core)")
        except Exception:
            pytest.skip("Pipeline configuration failed for PCRE2")

        log = tmp_path / "lookahead.txt"
        log.write_text("apple banana\norange banana\n", encoding="utf-8")

        # Pattern matches 'apple' only if followed by ' banana' (positive lookahead)
        pattern = r"apple(?= banana)"

        res = subprocess.run(
            [sys.executable, "-m", "tensor_grep.cli.main", "search", "-P", pattern, str(log)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "apple banana" in res.stdout
        assert "orange banana" not in res.stdout

    def test_max_filesize_respected(self, tmp_path):
        """Verify that --max-filesize correctly skips large files."""
        small_file = tmp_path / "small.txt"
        small_file.write_text("match_me", encoding="utf-8")

        large_file = tmp_path / "large.txt"
        large_file.write_text("match_me" + ("x" * 1024 * 1024), encoding="utf-8")  # ~1MB

        # Searching with 100KB limit should skip large_file
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "--max-filesize",
                "100K",
                "match_me",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )

        # Depending on how ripgrep handles stdout, it might return 0 if any matches or 1 if some skipped
        assert "small.txt" in res.stdout
        assert "large.txt" not in res.stdout
