import logging
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import managed_runtime_candidate, native_frontdoor
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec
from tensor_grep.cli.managed_runtime_reader import read_managed_runtime
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, encode_selection

pytestmark = pytest.mark.skipif(os.name != "nt", reason="managed runtime staging is Windows-only")


def _restricted_file(path: Path, payload: bytes) -> None:
    sid = winsec.current_user_sid()
    assert sid is not None
    handle = winsec.create_new_restricted(str(path), sid)
    assert handle is not None
    try:
        assert winsec.write_all(handle, payload)
    finally:
        winsec.close_handle(handle)


def _install_root(trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    profile = trusted_base_dir / f"managed-stage-{tmp_path.name}"
    root = profile / ".tensor-grep"
    assert trust._ensure_secret_dir(profile)
    assert trust._ensure_secret_dir(root)
    monkeypatch.setattr(managed_runtime_candidate, "_windows_profile_directory", lambda: profile)
    legacy_python = root / ".venv" / "Scripts" / "python.exe"
    legacy_python.parent.mkdir(parents=True)
    _restricted_file(legacy_python, b"legacy python")
    return root, legacy_python


def _seed_selected(root: Path, generation: str, version: str) -> None:
    generation_root = root / "runtimes" / generation
    python = generation_root / ".venv" / "Scripts" / "python.exe"
    native = generation_root / "bin" / "tg.exe"
    python.parent.mkdir(parents=True)
    native.parent.mkdir(parents=True)
    _restricted_file(python, b"selected python")
    _restricted_file(native, b"selected native")
    cli_main.atomic_write_bytes_anchored(
        root / "runtime-selection.json",
        encode_selection(ManagedRuntimeSelection(generation, version)),
    )


def _stub_stage_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fallback: bool = False,
    verify_error: BaseException | None = None,
    source_cuda: str | None = None,
):
    venv_calls: list[list[str]] = []
    installs: list[tuple[Path, dict[str, object]]] = []
    verification: list[tuple[Path, str, str | None, tuple[str, ...]]] = []
    candidate_installs: list[tuple[str | None, tuple[str, ...], tuple[str, ...]]] = []

    def run(args, **_kwargs):
        venv_calls.append(args)
        if fallback and args[0] == "uv":
            raise FileNotFoundError("uv unavailable")
        venv_path = Path(args[-1])
        python = venv_path / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"candidate python")

    def install(
        _python: Path,
        _spec: str,
        *,
        source_cuda: str | None = None,
        source_torch_auxiliaries: tuple[str, ...] = (),
        source_extras: tuple[str, ...] = (),
    ) -> str:
        candidate_installs.append((source_cuda, source_torch_auxiliaries, source_extras))
        return "pip"

    def verify(
        path: Path,
        version: str,
        *,
        expected_cuda: str | None = None,
        expected_torch_auxiliaries: tuple[str, ...] = (),
    ) -> None:
        verification.append((path, version, expected_cuda, expected_torch_auxiliaries))
        if verify_error is not None:
            raise verify_error

    def install_native(version: str, destination: Path, **kwargs):
        installs.append((destination, kwargs))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"candidate or stable native")
        return native_frontdoor._NativeFrontdoorInstallResult("local", "cpu", "tg.exe")

    monkeypatch.setattr(cli_main.subprocess, "run", run)
    monkeypatch.setattr(managed_runtime_candidate, "_run_managed_candidate_install", install)
    monkeypatch.setattr(
        managed_runtime_candidate,
        "_managed_candidate_package_spec",
        lambda spec, **_kwargs: spec,
    )
    monkeypatch.setattr(
        managed_runtime_candidate,
        "_managed_candidate_profile",
        lambda _executable: managed_runtime_candidate.ManagedCandidateProfile(
            "1.2.3", ("gpu-win",), source_cuda, ("torchvision",)
        ),
    )
    monkeypatch.setattr(managed_runtime_candidate, "_verify_managed_candidate_python", verify)
    monkeypatch.setattr(native_frontdoor, "_install_release_native_frontdoor", install_native)
    monkeypatch.setattr(
        native_frontdoor, "_stable_dispatcher_matches_release", lambda *_args: False
    )
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: None)
    monkeypatch.setattr(native_frontdoor, "_schedule_managed_activation", lambda *_args: None)
    return venv_calls, installs, verification, candidate_installs


def test_ManagedStage_WhenUvSeedsPackages_DoesNotShareCacheFiles(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in ("pip", "setuptools", "wheel"):
        wheel = wheels / f"{name}-99.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "x") as archive:
            archive.writestr(f"{name}/__init__.py", b'PROBE = "before"\n')
            archive.writestr(
                f"{name}-99.0.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: 99.0\n\n",
            )
            archive.writestr(
                f"{name}-99.0.dist-info/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
            )
            archive.writestr(f"{name}-99.0.dist-info/RECORD", "")

    cache = tmp_path / "cache"
    monkeypatch.delenv("UV_NO_CACHE", raising=False)
    for name, value in {
        "UV_CACHE_DIR": str(cache),
        "UV_FIND_LINKS": str(wheels),
        "UV_NO_INDEX": "1",
        "UV_OFFLINE": "1",
        "UV_NO_CONFIG": "1",
        "UV_VENV_SEED": "1",
        "UV_LINK_MODE": "hardlink",
    }.items():
        monkeypatch.setenv(name, value)
    run_process = subprocess.run

    def run(*args, **kwargs):
        kwargs.setdefault("timeout", 60)
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return run_process(*args, **kwargs)

    control = tmp_path / "control"
    run([uv, "venv", "--python", sys.executable, str(control)], check=True, capture_output=True)
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    executable.write_bytes((control / "Scripts" / "python.exe").read_bytes())
    shutil.copyfile(control / "pyvenv.cfg", root / ".venv" / "pyvenv.cfg")
    _stub_stage_dependencies(monkeypatch)
    monkeypatch.setattr(cli_main.subprocess, "run", run)
    result = native_frontdoor.stage_managed_windows_runtime(
        str(executable), "tensor-grep==1.2.3", "1.2.3"
    )

    control_file = control / "Lib" / "site-packages" / "pip" / "__init__.py"
    selected_file = (
        root
        / "runtimes"
        / result.generation
        / ".venv"
        / "Lib"
        / "site-packages"
        / "pip"
        / "__init__.py"
    )
    assert control_file.read_bytes() == selected_file.read_bytes() == b'PROBE = "before"\n'
    aliases = [path for path in cache.rglob("__init__.py") if os.path.samefile(path, control_file)]
    assert len(aliases) == 1
    control_alias = selected_file.with_name("fixture-control.py")
    os.link(control_file, control_alias)
    try:
        assert os.path.samefile(control_file, control_alias)
        aliases[0].write_bytes(b'PROBE = "changed"\n')
        assert control_file.read_bytes() == b'PROBE = "changed"\n'
        assert control_alias.read_bytes() == b'PROBE = "changed"\n'
        assert selected_file.read_bytes() == b'PROBE = "before"\n'
    finally:
        control_alias.unlink()


@pytest.mark.parametrize("existing_runtimes", [False, True], ids=["new", "existing"])
def test_ManagedStage_ProtectsNewDirectoriesFromInheritedWriteGrants(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_runtimes: bool
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    icacls = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
    subprocess.run(
        [str(icacls), str(root / ".venv"), "/inheritance:d"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    subprocess.run(
        [str(icacls), str(root), "/grant", "*S-1-1-0:(OI)(CI)(IO)(W)"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    control = root / "ordinary-child"
    control.mkdir()
    handle = winsec.open_no_follow(str(control), directory=True)
    assert handle is not None
    try:
        details = winsec.owner_and_dacl_entries(handle)
        assert details is not None
        assert not trust._windows_parent_dacl_ok(details[1])
    finally:
        winsec.close_handle(handle)

    runtimes = root / "runtimes"
    run = subprocess.run
    original_acl = None
    if existing_runtimes:
        sid = winsec.current_user_sid()
        assert sid is not None
        assert winsec.create_directory_restricted(str(runtimes), sid, exist_ok=False)
        run(
            [str(icacls), str(runtimes), "/grant", "*S-1-1-0:(OI)(CI)(IO)(W)"],
            capture_output=True,
            check=True,
            timeout=15,
        )
        original_acl = run(
            [str(icacls), str(runtimes)], capture_output=True, check=True, timeout=15
        ).stdout

    _stub_stage_dependencies(monkeypatch, verify_error=RuntimeError("candidate stop"))
    with pytest.raises(RuntimeError, match="candidate stop"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    if original_acl is not None:
        assert (
            run([str(icacls), str(runtimes)], capture_output=True, check=True, timeout=15).stdout
            == original_acl
        )
    generations = list(runtimes.iterdir())
    assert len(generations) == 1
    for directory in (runtimes, generations[0], generations[0] / ".venv"):
        handle = winsec.open_no_follow(str(directory), directory=True)
        assert handle is not None
        try:
            details = winsec.owner_and_dacl_entries(handle)
            assert details is not None
            assert trust._windows_parent_dacl_ok(details[1])
        finally:
            winsec.close_handle(handle)
    assert not (root / "runtime-selection.json").exists()


def test_RestrictedDirectory_ExistingPathHonorsExclusiveCreation(tmp_path: Path) -> None:
    sid = winsec.current_user_sid()
    assert sid is not None
    path = tmp_path / "candidate"
    assert winsec.create_directory_restricted(str(path), sid, exist_ok=False)
    assert winsec.create_directory_restricted(str(path), sid)
    assert not winsec.create_directory_restricted(str(path), sid, exist_ok=False)


@pytest.mark.parametrize("failure", ["no-sid", "create-failed"])
def test_ManagedStage_WhenDirectoryCreationUnavailable_StopsBeforeCreatingRuntime(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    calls, _installs, _verification, _candidate_installs = _stub_stage_dependencies(monkeypatch)
    if failure == "no-sid":
        monkeypatch.setattr(winsec, "current_user_sid", lambda: None)
    else:
        monkeypatch.setattr(winsec, "create_directory_restricted", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="managed runtimes directory cannot be created"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert calls == []
    assert not (root / "runtimes").exists()
    assert not (root / "runtime-selection.json").exists()


def test_ManagedStage_RefusesExistingRuntimesWithForeignWriteAccess(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    runtimes = root / "runtimes"
    runtimes.mkdir()
    icacls = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
    subprocess.run(
        [str(icacls), str(runtimes), "/grant", "*S-1-1-0:(W)"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    calls, _installs, _verification, _candidate_installs = _stub_stage_dependencies(monkeypatch)

    with pytest.raises(RuntimeError, match="directory ACL is not trusted"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert calls == []
    assert list(runtimes.iterdir()) == []
    assert not (root / "runtime-selection.json").exists()


def test_ManagedStage_WhenHandleCleanupFails_LogsWarning(
    trusted_base_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _stub_stage_dependencies(monkeypatch)

    class FailingCloseExitStack(native_frontdoor.ExitStack):
        def close(self) -> None:
            super().close()
            raise OSError("fixture close failure")

    monkeypatch.setattr(native_frontdoor, "ExitStack", FailingCloseExitStack)
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: 1)
    monkeypatch.setattr(
        native_frontdoor,
        "_schedule_managed_activation",
        lambda *_args: tmp_path / "activation.log",
    )

    with caplog.at_level(logging.WARNING, logger=native_frontdoor.__name__):
        result = native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert result.activation_pending
    assert "managed runtime staging cleanup failed (OSError)" in caplog.text
    assert "fixture close failure" not in caplog.text


@pytest.mark.parametrize(
    ("fallback", "expected_venv_calls"),
    [(False, 1), (True, 2)],
    ids=["uv", "python-venv-fallback"],
)
def test_ManagedStage_WhenCandidateVerified_RepairsLauncherThenPublishes(
    trusted_base_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
    expected_venv_calls: int,
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    venv_calls, installs, verification, _candidate_installs = _stub_stage_dependencies(
        monkeypatch, fallback=fallback
    )

    result = native_frontdoor.stage_managed_windows_runtime(
        str(executable), "tensor-grep==1.2.3", "1.2.3"
    )

    selected = read_managed_runtime(trusted_root=root)
    assert selected is not None
    assert selected.selection.generation == result.generation
    assert selected.selection.version == "1.2.3"
    assert result.install_method == "pip"
    assert result.native_flavor == "cpu"
    assert not result.activation_pending
    assert len(venv_calls) == expected_venv_calls
    assert venv_calls[0][0] == "uv"
    assert venv_calls[0][4:6] == ["--link-mode", "copy"]
    assert venv_calls[-1][0] == (str(executable) if fallback else "uv")
    assert isinstance(installs[0][1].pop("_managed_held"), ExitStack)
    assert installs[0][1] == {
        "verify_destination": False,
        "_managed_source": True,
        "_fresh_destination": True,
    }
    assert installs[1][0] == root / "bin" / "tg.exe"
    assert installs[1][1] == {"verify_destination": False, "_managed_source": True}
    assert verification == [
        (
            root / "runtimes" / result.generation / ".venv" / "Scripts" / "python.exe",
            "1.2.3",
            None,
            (),
        )
    ]


@pytest.mark.parametrize(("source_cuda", "expected_cuda"), [(None, "cpu"), ("12.8", "12.8")])
def test_ManagedStage_WhenGpuWinSourceProfileIsKnown_PreservesAndVerifiesIt(
    trusted_base_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_cuda: str | None,
    expected_cuda: str,
) -> None:
    _root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _venv_calls, _installs, verification, candidate_installs = _stub_stage_dependencies(
        monkeypatch, source_cuda=source_cuda
    )

    native_frontdoor.stage_managed_windows_runtime(str(executable), "tensor-grep[gpu-win]", "1.2.3")

    assert candidate_installs == [(source_cuda, ("torchvision",), ("gpu-win",))]
    assert verification[0][2] == expected_cuda
    assert verification[0][3] == ("torchvision",)


def test_ManagedActivation_ReadinessBudgetCoversTorchProbeBound() -> None:
    calls = []
    handles = object()
    kernel = SimpleNamespace(WaitForMultipleObjects=lambda *args: calls.append(args) or 0)

    native_frontdoor._wait_for_managed_activation_ready(kernel, handles)

    assert calls == [(2, handles, False, 90_000)]


def test_ManagedStage_WhenNativeParentLives_ReturnsPendingAfterReadiness(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _stub_stage_dependencies(monkeypatch)
    status = root / "logs" / "activation-pending.log"
    scheduled: list[tuple[object, ...]] = []
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: 987)

    def schedule(*args):
        scheduled.append(args)
        return status

    monkeypatch.setattr(native_frontdoor, "_schedule_managed_activation", schedule)

    result = native_frontdoor.stage_managed_windows_runtime(
        str(executable), "tensor-grep==1.2.3", "1.2.3"
    )

    assert result.activation_pending
    assert result.activation_log == status
    assert scheduled[0][4] == 987
    assert (root / "runtimes" / result.generation).is_dir()
    assert read_managed_runtime(trusted_root=root) is None


def test_ManagedStage_WhenCandidateVerificationFails_PreservesPreviousSelection(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _seed_selected(root, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.1.0")
    _stub_stage_dependencies(monkeypatch, verify_error=RuntimeError("candidate invalid"))

    with pytest.raises(RuntimeError, match="candidate invalid"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    selected = read_managed_runtime(trusted_root=root)
    assert selected is not None
    assert selected.selection.version == "1.1.0"
    assert len(list((root / "runtimes").iterdir())) == 2


def test_ManagedStage_WhenStableDispatcherRepairFails_PreservesPreviousSelector(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _seed_selected(root, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.1.0")
    previous = (root / "runtime-selection.json").read_bytes()
    _stub_stage_dependencies(monkeypatch)
    install_native = native_frontdoor._install_release_native_frontdoor

    def fail_stable_repair(version: str, destination: Path, **kwargs):
        if destination == root / "bin" / "tg.exe":
            raise RuntimeError("stable dispatcher repair failed")
        return install_native(version, destination, **kwargs)

    monkeypatch.setattr(native_frontdoor, "_install_release_native_frontdoor", fail_stable_repair)

    with pytest.raises(RuntimeError, match="stable dispatcher repair failed"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert (root / "runtime-selection.json").read_bytes() == previous


def test_ManagedStage_WhenFreshNativeCollision_PreservesSelector(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import urllib.request
    from uuid import uuid4

    from tests.unit.test_native_download_collision import _configure

    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _seed_selected(root, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.1.0")
    previous = (root / "runtime-selection.json").read_bytes()
    install_native = native_frontdoor._install_release_native_frontdoor
    _stub_stage_dependencies(monkeypatch)
    _configure(monkeypatch, b"verified asset")
    monkeypatch.setattr(native_frontdoor, "uuid4", uuid4)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"verified asset")
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _path: "1.2.3")
    occupied = []

    def collide(version, destination, **kwargs):
        destination.write_bytes(b"existing object")
        occupied.append(destination)
        return install_native(version, destination, **kwargs)

    monkeypatch.setattr(native_frontdoor, "_install_release_native_frontdoor", collide)
    with pytest.raises(RuntimeError, match="destination appeared") as failure:
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )
    assert isinstance(failure.value.__cause__, FileExistsError)
    assert (root / "runtime-selection.json").read_bytes() == previous
    assert occupied[0].read_bytes() == b"existing object"


def test_ManagedStage_WhenCandidateIsOlder_SkipsStableRepairAndPreservesSelection(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _seed_selected(root, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "1.3.0")
    previous = (root / "runtime-selection.json").read_bytes()
    dispatcher = b"previous stable dispatcher"
    stable_path = root / "bin" / "tg.exe"
    stable_path.parent.mkdir()
    _restricted_file(stable_path, dispatcher)
    _venv_calls, installs, _verification, _candidate_installs = _stub_stage_dependencies(
        monkeypatch
    )

    with pytest.raises(RuntimeError, match="refusing to activate older candidate"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert len(installs) == 1
    assert (root / "runtime-selection.json").read_bytes() == previous
    assert stable_path.read_bytes() == dispatcher


def test_ManagedStage_WhenSchedulerSettlesFailure_DoesNotPublish(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _seed_selected(root, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "1.1.0")
    _stub_stage_dependencies(monkeypatch)
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: 123)
    monkeypatch.setattr(
        native_frontdoor,
        "_schedule_managed_activation",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("worker settled")),
    )

    with pytest.raises(RuntimeError, match="worker settled"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert read_managed_runtime(trusted_root=root).selection.version == "1.1.0"


def _unsettled_error() -> BaseException:
    return native_frontdoor._ManagedActivationUnsettledError("helper may live")


def _marked_interrupt() -> BaseException:
    error = KeyboardInterrupt()
    setattr(error, native_frontdoor._MANAGED_ACTIVATION_WORKER_MAY_LIVE, True)
    return error


@pytest.mark.parametrize(
    "error_factory",
    [_unsettled_error, _marked_interrupt],
    ids=["unsettled-error", "marked-interrupt"],
)
def test_ManagedStage_WhenSchedulerMayLive_RetainsGeneration(
    trusted_base_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_factory,
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _stub_stage_dependencies(monkeypatch)
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: 123)
    error = error_factory()
    monkeypatch.setattr(
        native_frontdoor,
        "_schedule_managed_activation",
        lambda *_args: (_ for _ in ()).throw(error),
    )

    with pytest.raises(BaseException) as caught:
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert caught.value is error
    assert next((root / "runtimes").iterdir()).is_dir()


def test_ManagedStage_WhenPublicationWasAttempted_RetainsGeneration(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    _stub_stage_dependencies(monkeypatch)

    def refuse_after_attempt(_root, _generation, _version, _held, attempted):
        attempted[0] = True
        raise RuntimeError("selector replacement refused")

    monkeypatch.setattr(native_frontdoor, "_publish_managed_generation", refuse_after_attempt)

    with pytest.raises(RuntimeError, match="selector replacement refused"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert next((root / "runtimes").iterdir()).is_dir()


def test_ManagedStage_WhenGenerationAlreadyExists_DoesNotDeleteIt(
    trusted_base_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    generation = "cccccccccccccccccccccccccccccccc"
    existing = root / "runtimes" / generation
    existing.mkdir(parents=True)
    sentinel = existing / "owner-data.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(native_frontdoor, "uuid4", lambda: SimpleNamespace(hex=generation))
    calls, _installs, _verification, _candidate_installs = _stub_stage_dependencies(monkeypatch)

    with pytest.raises(RuntimeError, match="generation already exists or cannot be created"):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert calls == []
    assert sentinel.read_text(encoding="utf-8") == "keep"
