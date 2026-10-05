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

    # `tree-sitter` lives in the OPTIONAL `ast` extra, so a plain `pip install tensor-grep` has no
    # grammar. Tests that need one are then misleading in BOTH directions: the positive ones fail
    # with messages that read like product bugs ("expected at least one reference to X"), and the
    # EXCLUSION ones pass VACUOUSLY -- nothing can parse, so nothing is found, so the exclusion
    # "holds". Measured 2026-08-02: 113 failures across 9 files, plus vacuous passers such as
    # test_declarator_shape_4_function_pointer_variable_is_excluded.
    #
    # Marker, not a module-level importorskip: every affected file MIXES grammar-dependent tests
    # with parser-independent ones (static registry metadata, missing-file/wrong-suffix
    # short-circuits) AND with a deliberate `*_grammar_absent_*` family that must keep running in
    # BOTH environments. A module gate would have skipped ~338 legitimately-passing tests.
    #
    # CI installs `.[dev]`, which contains tree-sitter, so this never fires there -- no coverage
    # is lost; it only stops a parser-less dev env from reporting env problems as product bugs.
    if not _tree_sitter_available():
        skip_grammar = pytest.mark.skip(reason="needs the optional `ast` extra (tree-sitter)")
        for item in items:
            if "requires_grammar" in item.keywords:
                item.add_marker(skip_grammar)


def _tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return False
    return True


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


def _rmtree_retry(path: Path) -> None:
    """Best-effort ``rmtree`` that retries: a just-killed daemon can still hold its cwd on Windows."""
    import time

    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.25)


@pytest.fixture(autouse=True, scope="session")
def _isolated_daemon_secret_dir(tmp_path_factory):
    """Point the session daemon's per-user HMAC secret at a throwaway dir for the whole run.

    The daemon ping proof (``session_daemon_trust``) reads/creates a per-user secret in the real
    user state dir; without this every real-daemon test would write the developer's actual
    ``~/.local/state/tensor-grep`` / ``%LOCALAPPDATA%``. Session scope + direct ``os.environ`` (not
    ``monkeypatch``, see the fixture docstring below for why) means spawned daemon subprocesses
    inherit the same dir as the in-process client. A test-local ``TG_DAEMON_SECRET_DIR`` set via
    ``monkeypatch`` still overrides it for that test.
    """
    key = "TG_DAEMON_SECRET_DIR"
    created = key not in os.environ
    fallback: Path | None = None
    if created:
        chosen, fallback = _secret_dir_with_trusted_ancestors(tmp_path_factory.mktemp("tg-secret"))
        os.environ[key] = str(chosen)
    try:
        yield
    finally:
        if created:
            os.environ.pop(key, None)
        if fallback is not None:
            _rmtree_retry(fallback)


def _secret_dir_with_trusted_ancestors(preferred: Path) -> tuple[Path, Path | None]:
    """``(secret_dir, fallback_to_delete)`` for the daemon secret, honouring the product's ancestor-trust walk.

    The daemon refuses to create or read its secret when ANY ancestor directory grants a foreign
    principal modify rights (``session_daemon_trust._ancestors_refusal``). That is correct product
    behaviour, but it makes ``tmp_path`` an environment-dependent place for the secret: on a Windows
    dev box whose ``%TEMP%`` carries inherited grants for sandbox/service accounts (e.g.
    ``CodexSandboxUsers``) every real-daemon test fails with "cannot establish per-user daemon
    secret", while GitHub's runners pass. Where ``preferred`` is refused, fall back to a throwaway
    directory under ``%LOCALAPPDATA%\\tensor-grep`` (the product's own default location, whose
    ancestors the product itself must accept). If that is refused too, keep ``preferred`` and let the
    test fail honestly.
    """
    if sys.platform != "win32" or not os.environ.get("LOCALAPPDATA"):
        return preferred, None
    from tensor_grep.cli import session_daemon_trust as trust

    if trust._ancestors_refusal(preferred) is None:
        return preferred, None
    import uuid

    fallback = (
        Path(os.environ["LOCALAPPDATA"]) / "tensor-grep" / f"pytest-secret-{uuid.uuid4().hex}"
    )
    fallback.mkdir(parents=True, exist_ok=True)  # a MISSING ancestor is refused, so create first
    if trust._ancestors_refusal(fallback / "secret") is not None:
        shutil.rmtree(fallback, ignore_errors=True)
        return preferred, None
    return fallback / "secret", fallback


@pytest.fixture
def tmp_path(request, tmp_path):
    """``tmp_path``, except in ``test_session_daemon*`` modules, which build secret directories and
    ancestor chains under it and so need a root the product's ancestor-trust walk accepts.

    Where the default ``tmp_path`` is accepted (CI, POSIX) this is the same directory. On a Windows
    box whose %TEMP% carries foreign Modify grants it is a throwaway directory under
    ``%LOCALAPPDATA%\\tensor-grep`` instead, so a hostile element a test plants is the only possible
    reason for a refusal. Removed after the test.
    """
    if not request.module.__name__.rpartition(".")[2].startswith("test_session_daemon"):
        yield tmp_path
        return
    chosen, fallback = _secret_dir_with_trusted_ancestors(tmp_path / "secret")
    try:
        yield chosen.parent if fallback is not None else tmp_path
    finally:
        if fallback is not None:
            _rmtree_retry(fallback)


@pytest.fixture
def trusted_base_dir(trusted_daemon_secret_dir):
    """An existing directory whose own ancestor chain the product accepts: a drop-in for
    ``tmp_path`` in tests that BUILD an ancestor chain (and plant a hostile element in it), so the
    only possible reason for a refusal is the element the test planted, not the machine's %TEMP%.

    Equal to ``tmp_path`` wherever ``tmp_path`` is itself trusted. The built-in control below makes
    "this base is accepted on its own" a failing assertion rather than an assumption.
    """
    base = trusted_daemon_secret_dir.parent
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        from tensor_grep.cli import session_daemon_trust as trust

        assert trust._ancestors_refusal(base) is None, "no trusted base exists on this machine"
    return base


@pytest.fixture
def trusted_daemon_secret_dir(tmp_path):
    """A per-test secret dir the product's ancestor-trust walk accepts (see the helper above)."""
    chosen, fallback = _secret_dir_with_trusted_ancestors(tmp_path / "secret")
    try:
        yield chosen
    finally:
        if fallback is not None:
            _rmtree_retry(fallback)


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


@pytest.fixture(autouse=True)
def _host_kernel_is_not_a_wsl_signal():
    """``runtime_paths.is_wsl_host`` falls back to the ``/proc/version`` "microsoft" stamp, which
    is a property of the HOST KERNEL: every Linux container on Docker Desktop runs on the WSL2
    kernel and reports it. Unpinned, 13 tests that model a non-WSL Linux box (GPU doctor probes,
    the bare-Linux-CI regression guard, ...) failed only inside scripts/ci-local. Tests express
    WSL-ness through WSL_DISTRO_NAME / WSL_INTEROP / ``/run/WSL``; the kernel-stamp helper is
    tested directly in test_runtime_paths.py. Same no-``monkeypatch`` rule as the fixture above.
    """
    from tensor_grep.cli import runtime_paths

    original = runtime_paths._kernel_reports_wsl
    runtime_paths._kernel_reports_wsl = lambda: False
    try:
        yield
    finally:
        runtime_paths._kernel_reports_wsl = original
