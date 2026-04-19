import os
import sys
import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.performance]


def cpu_backend_throughput_floor(
    github_actions: str | None = None,
    platform_name: str | None = None,
) -> float | None:
    github_actions = github_actions if github_actions is not None else os.getenv("GITHUB_ACTIONS")
    platform_name = platform_name if platform_name is not None else sys.platform

    if github_actions and platform_name.startswith("win"):
        return None

    return 8.0


class TestThroughput:
    def test_cpu_backend_throughput(self, tmp_path):
        """Baseline: CPU backend should sustain a minimum local throughput floor."""
        floor = cpu_backend_throughput_floor()
        if floor is None:
            pytest.skip(
                "Hosted Windows GitHub Actions runners are too noisy for this local throughput smoke floor; "
                "benchmark-regression remains the blocking performance gate."
            )

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
        assert throughput > floor, f"CPU throughput {throughput:.1f} MB/s below {floor:.1f} MB/s"
