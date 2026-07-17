import gc
import os
import sys
import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.performance]

_UNSET = object()


def cpu_backend_throughput_floor(
    github_actions: str | None | object = _UNSET,
    platform_name: str | None | object = _UNSET,
) -> float | None:
    if github_actions is _UNSET:
        github_actions = os.getenv("GITHUB_ACTIONS")
    if platform_name is _UNSET:
        platform_name = sys.platform

    if platform_name.startswith("win"):
        return None

    return 8.0


def test_cpu_backend_throughput_floor_skips_hosted_windows_actions() -> None:
    assert cpu_backend_throughput_floor(github_actions="true", platform_name="win32") is None


def test_cpu_backend_throughput_floor_skips_local_windows_actions() -> None:
    assert cpu_backend_throughput_floor(github_actions="", platform_name="win32") is None


class TestThroughput:
    def test_cpu_backend_throughput(self, tmp_path):
        """Baseline: CPU backend should sustain a minimum local throughput floor."""
        floor = cpu_backend_throughput_floor()

        if floor is None:
            pytest.skip(
                "Windows runners are too noisy for this local throughput smoke floor; "
                "benchmark-regression remains the blocking performance gate."
            )

        large = tmp_path / "large.log"
        lines = "2026-02-24 ERROR test line content here\n" * 100_000
        large.write_text(lines)

        from tensor_grep.backends.cpu_backend import CPUBackend

        backend = CPUBackend()
        mb = large.stat().st_size / (1024 * 1024)

        def best_of(samples: int) -> float:
            throughputs: list[float] = []
            for _ in range(samples):
                start = time.perf_counter()
                backend.search(str(large), "ERROR")
                elapsed = time.perf_counter() - start
                throughputs.append(mb / elapsed)
            return max(throughputs)

        # The first run on a loaded developer machine is noisy enough to make a
        # single-sample threshold flaky. Warm once, then keep the best of a
        # small bounded sample set. Force a collection before timing so earlier
        # e2e allocations do not trigger GC inside the measured window.
        backend.search(str(large), "ERROR")
        gc.collect()
        throughput = best_of(6)

        # A shared hosted CI runner can have a noisy-neighbor spike that drags
        # down every sample in one short measurement window even at best-of-6
        # (this floor already skips Windows CI runners for the same class of
        # noise -- see cpu_backend_throughput_floor). Give the runner one
        # bounded second wave to recover from a transient spike before
        # failing: a genuine throughput regression will still miss the floor
        # on the second wave too, so this does not weaken the floor's power
        # to catch a real regression.
        if throughput <= floor:
            gc.collect()
            throughput = max(throughput, best_of(6))

        # This is a sanity floor for shared developer/CI machines, not a
        # benchmark claim. Hot-path performance work is tracked through the
        # dedicated benchmark suite, not this smoke test.
        assert throughput > floor, f"CPU throughput {throughput:.1f} MB/s below {floor:.1f} MB/s"
