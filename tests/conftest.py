import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

existing_pythonpath = os.environ.get("PYTHONPATH", "")
pythonpath_entries = [entry for entry in existing_pythonpath.split(os.pathsep) if entry]
if str(SRC_DIR) not in pythonpath_entries:
    os.environ["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), *pythonpath_entries])


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


@pytest.fixture(autouse=True)
def cleanup_external_lsp_providers():
    yield
    repo_map_module = sys.modules.get("tensor_grep.cli.repo_map")
    if repo_map_module is None:
        return
    manager = getattr(repo_map_module, "_EXTERNAL_LSP_PROVIDER_MANAGER", None)
    if manager is not None:
        manager.stop_all()


@pytest.fixture(autouse=True)
def _disable_session_daemon_autostart_by_default():
    """Task #94 PR-1 trap T3: TG_SESSION_DAEMON_AUTOSTART now defaults ON (opt-out, see
    ``_session_daemon_autostart_enabled`` in ``src/tensor_grep/cli/main.py``). Without this,
    hundreds of unrelated CliRunner tests across the suite that invoke defs/impact/refs/callers/
    blast-radius would each try to autostart a REAL background session-daemon subprocess on a
    dev box -- the CI/GITHUB_ACTIONS force-off baked into that function does not cover a local
    ``pytest`` run. Force the flag off for the whole suite; individual daemon tests (see
    ``tests/unit/test_symbol_daemon_autostart.py``) opt back in per-test via their own
    ``monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")``, which overrides the value set
    here for the remainder of that test only (restored below on teardown either way).

    Deliberately does NOT take a ``monkeypatch`` fixture parameter -- taking one here would pull
    ``monkeypatch`` into this autouse fixture's setup, which changes ITS position (and therefore
    ``monkeypatch``'s own teardown position) in pytest's per-test fixture finalization stack
    relative to every OTHER fixture that also depends on ``monkeypatch``, including a test's own
    explicit ``monkeypatch`` parameter. That reordering was verified to break the existing
    ``cleanup_external_lsp_providers`` fixture above: in
    ``tests/unit/test_semantic_provider_navigation.py`` tests that
    ``monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())``, adding a
    monkeypatch-dependent autouse fixture here made ``cleanup_external_lsp_providers``'s teardown
    run BEFORE ``monkeypatch`` reverted that attribute, so it called ``.stop_all()`` on the test's
    fake manager instead of on ``None`` -- ``AttributeError: '_FakeManager' object has no
    attribute 'stop_all'``. Save/restore ``os.environ`` directly instead, which keeps this
    fixture dependency-free and leaves the pre-existing fixture graph untouched.
    """
    previous = os.environ.get("TG_SESSION_DAEMON_AUTOSTART")
    os.environ["TG_SESSION_DAEMON_AUTOSTART"] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TG_SESSION_DAEMON_AUTOSTART", None)
        else:
            os.environ["TG_SESSION_DAEMON_AUTOSTART"] = previous
