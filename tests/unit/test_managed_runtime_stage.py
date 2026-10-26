import os
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
):
    venv_calls: list[list[str]] = []
    installs: list[tuple[Path, dict[str, object]]] = []
    verification: list[tuple[Path, str, str | None]] = []

    def run(args, **_kwargs):
        venv_calls.append(args)
        if fallback and args[0] == "uv":
            raise FileNotFoundError("uv unavailable")
        venv_path = Path(args[-1])
        python = venv_path / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"candidate python")

    def install(_python: Path, _spec: str) -> str:
        return "pip"

    def verify(path: Path, version: str, *, expected_cuda: str | None = None) -> None:
        verification.append((path, version, expected_cuda))
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
        managed_runtime_candidate, "_managed_candidate_package_spec", lambda spec: spec
    )
    monkeypatch.setattr(managed_runtime_candidate, "_verify_managed_candidate_python", verify)
    monkeypatch.setattr(native_frontdoor, "_install_release_native_frontdoor", install_native)
    monkeypatch.setattr(
        native_frontdoor, "_stable_dispatcher_matches_release", lambda *_args: False
    )
    monkeypatch.setattr(native_frontdoor, "_open_native_parent_handle", lambda _path: None)
    monkeypatch.setattr(native_frontdoor, "_schedule_managed_activation", lambda *_args: None)
    return venv_calls, installs, verification


@pytest.mark.parametrize(
    ("fallback", "expected_venv_calls"),
    [(False, 1), (True, 2)],
    ids=["uv", "python-venv-fallback"],
)
def test_ManagedStage_WhenCandidateVerified_PublishesThenRepairsLauncher(
    trusted_base_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
    expected_venv_calls: int,
) -> None:
    root, executable = _install_root(trusted_base_dir, tmp_path, monkeypatch)
    venv_calls, installs, verification = _stub_stage_dependencies(monkeypatch, fallback=fallback)

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
    assert venv_calls[-1][0] == (str(executable) if fallback else "uv")
    assert installs[0][1] == {"verify_destination": False}
    assert installs[1][0] == root / "bin" / "tg.exe"
    assert installs[1][1] == {}
    assert verification == [
        (root / "runtimes" / result.generation / ".venv" / "Scripts" / "python.exe", "1.2.3", None)
    ]


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

    with pytest.raises(RuntimeError):
        native_frontdoor.stage_managed_windows_runtime(
            str(executable), "tensor-grep==1.2.3", "1.2.3"
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
