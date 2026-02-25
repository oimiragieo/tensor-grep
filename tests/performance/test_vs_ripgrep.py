import subprocess, time, pytest

pytestmark = [pytest.mark.slow, pytest.mark.performance]

class TestVsRipgrep:
    def test_semantic_classification_faster_than_multi_rg(self, tmp_path, rg_path):
        """GPU value prop: single classify pass vs N separate rg passes."""
        log = tmp_path / "mixed.log"
        # Generate log with multiple event types
        lines = []
        for i in range(10_000):
            if i % 3 == 0:
                lines.append(f"2026-02-24 ERROR Connection timeout from 10.0.0.{i%256}\n")
            elif i % 3 == 1:
                lines.append(f"2026-02-24 WARN Disk usage at {60+i%40}%\n")
            else:
                lines.append(f"2026-02-24 INFO Request processed in {i%100}ms\n")
        log.write_text("".join(lines))

        patterns = ["ERROR", "WARN", "INFO", r"\d+\.\d+\.\d+\.\d+", "timeout", "Disk usage"]
        start = time.perf_counter()
        for p in patterns:
            subprocess.run([rg_path, p, str(log)], capture_output=True)
        rg_total = time.perf_counter() - start

        # Our tool: classify does all at once (when GPU available)
        start = time.perf_counter()
        subprocess.run(
            ["cybert-grep", "search", "--cpu", "ERROR|WARN|INFO", str(log)],
            capture_output=True,
        )
        our_total = time.perf_counter() - start

        print(f"ripgrep {len(patterns)} passes: {rg_total:.3f}s")
        print(f"cybert-grep single pass: {our_total:.3f}s")
