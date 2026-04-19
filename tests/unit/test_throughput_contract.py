from tests.e2e.test_throughput import cpu_backend_throughput_floor


def test_cpu_backend_throughput_floor_skips_hosted_windows_actions() -> None:
    assert cpu_backend_throughput_floor(github_actions="true", platform_name="win32") is None


def test_cpu_backend_throughput_floor_keeps_default_off_actions() -> None:
    assert cpu_backend_throughput_floor(github_actions=None, platform_name="win32") == 8.0


def test_cpu_backend_throughput_floor_keeps_default_on_non_windows_actions() -> None:
    assert cpu_backend_throughput_floor(github_actions="true", platform_name="linux") == 8.0
