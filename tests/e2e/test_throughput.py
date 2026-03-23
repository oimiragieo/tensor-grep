import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.performance]


class TestThroughput:
    def test_cpu_backend_throughput(self, tmp_path):
        """Baseline: CPU backend should sustain a minimum local throughput floor."""
        large = tmp_path / "large.log"
        lines = "2026-02-24 ERROR test line content here\n" * 100_000
        large.write_text(lines)

        from tensor_grep.backends.cpu_backend import CPUBackend

        backend = CPUBackend()
        mb = large.stat().st_size / (1024 * 1024)
        throughputs: list[float] = []

        # The first run on a loaded developer machine is noisy enough to make a
        # single-sample threshold flaky. Warm once, then keep the best of a
        # small bounded sample set.
        backend.search(str(large), "ERROR")
        for _ in range(3):
            start = time.perf_counter()
            backend.search(str(large), "ERROR")
            elapsed = time.perf_counter() - start
            throughputs.append(mb / elapsed)

        throughput = max(throughputs)

        # This is a sanity floor for shared developer/CI machines, not a
        # benchmark claim. Hot-path performance work is tracked through the
        # dedicated benchmark suite, not this smoke test.
        assert throughput > 8, f"CPU throughput {throughput:.1f} MB/s below 8 MB/s"
