import shutil

import pytest


def pytest_configure(config):
    try:
        import torch

        if not torch.cuda.is_available():
            raise ImportError
        config._gpu_available = True
    except ImportError:
        config._gpu_available = False


def pytest_collection_modifyitems(config, items):
    if not getattr(config, "_gpu_available", False):
        skip_gpu = pytest.mark.skip(reason="CUDA GPU not available")
        for item in items:
            if "gpu" in item.keywords:
                item.add_marker(skip_gpu)


@pytest.fixture
def sample_log_file(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        "2026-02-24 10:00:01 INFO Server started on port 8080\n"
        "2026-02-24 10:00:05 ERROR Connection timeout to database\n"
        "2026-02-24 10:00:06 WARN Retrying connection attempt 1/3\n"
        "2026-02-24 10:00:10 ERROR Failed SSH login from 192.168.1.100\n"
        "2026-02-24 10:00:15 INFO Request GET /api/users 200 12ms\n"
    )
    return log


@pytest.fixture
def rg_path():
    path = shutil.which("rg")
    if not path:
        pytest.skip("ripgrep not installed")
    return path
