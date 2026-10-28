import hashlib
import os
import subprocess
import sys
import types

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import managed_runtime_candidate, native_frontdoor
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec
from tensor_grep.cli.managed_runtime_reader import read_managed_runtime
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, encode_selection

pytestmark = pytest.mark.skipif(os.name != "nt", reason="managed activation is Windows-only")


def _trusted_install_root(trusted_base_dir, tmp_path):
    profile = trusted_base_dir / f"activation-worker-{tmp_path.name}"
    root = profile / ".tensor-grep"
    assert trust._ensure_secret_dir(profile)
    assert trust._ensure_secret_dir(root)
    assert trust._parent_trusted(root)
    return profile, root


def _create_generation(root, generation):
    generation_root = root / "runtimes" / generation
    (generation_root / "bin").mkdir(parents=True)
    (generation_root / ".venv" / "Scripts").mkdir(parents=True)
    (generation_root / "bin" / "tg.exe").write_bytes(b"candidate native")
    (generation_root / ".venv" / "Scripts" / "python.exe").write_bytes(b"candidate python")
    return generation_root


def _create_restricted_file(path, payload):
    sid = winsec.current_user_sid()
    assert sid is not None
    handle = winsec.create_new_restricted(str(path), sid)
    assert handle is not None
    try:
        assert winsec.write_all(handle, payload)
    finally:
        winsec.close_handle(handle)


def _exited_parent_handle(request):
    gate = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.readline()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    request.addfinalizer(lambda: _release_gate(gate))
    handle = winsec.open_process(gate.pid)
    assert handle is not None
    _release_gate(gate)
    return handle


def _release_gate(gate):
    if gate.poll() is None:
        assert gate.stdin is not None
        gate.stdin.write(b"exit\n")
        gate.stdin.flush()
        gate.wait(timeout=10)


def _prepare_worker(monkeypatch, profile, install_native):
    monkeypatch.setattr(managed_runtime_candidate, "_windows_profile_directory", lambda: profile)
    monkeypatch.setattr(
        managed_runtime_candidate,
        "_verify_managed_candidate_python",
        lambda _path, _version, **_kwargs: None,
    )
    monkeypatch.setattr(
        native_frontdoor, "_stable_dispatcher_matches_release", lambda *_args: False
    )
    monkeypatch.setattr(native_frontdoor, "_install_release_native_frontdoor", install_native)


def test_ActivationWorker_WhenParentHasExited_RepairsStableLauncherThenPublishes(
    monkeypatch, request, trusted_base_dir, tmp_path
):
    profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    _create_generation(root, generation)
    _prepare_worker(
        monkeypatch,
        profile,
        lambda _version, path, **kwargs: (
            path.write_bytes(b"stable")
            if kwargs == {"verify_destination": False}
            else pytest.fail(f"unexpected dispatcher install options: {kwargs}")
        ),
    )
    parent_handle = _exited_parent_handle(request)
    ready_handle = native_frontdoor._create_activation_ready_event()

    result = native_frontdoor._activate_managed_generation_after_parent(
        parent_handle, ready_handle, root, generation, "1.2.3"
    )

    selected = read_managed_runtime(trusted_root=root)
    assert result == 0
    assert selected is not None
    assert selected.selection.generation == generation
    assert selected.selection.version == "1.2.3"
    assert (root / "bin" / "tg.exe").read_bytes() == b"stable"
    assert (
        (root / "logs" / f"activation-{generation}.log").read_text().startswith("activated 1.2.3")
    )


def test_ActivationWorker_WhenStableDispatcherRepairFails_PreservesPreviousSelector(
    monkeypatch, request, trusted_base_dir, tmp_path
):
    profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    old_generation = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    generation = "0123456789abcdef0123456789abcdef"
    for name in (old_generation, generation):
        _create_generation(root, name)
    cli_main.atomic_write_bytes_anchored(
        root / "runtime-selection.json",
        encode_selection(ManagedRuntimeSelection(old_generation, "1.1.0")),
    )
    previous = (root / "runtime-selection.json").read_bytes()

    def fail_repair(_version, _path, **kwargs):
        assert kwargs == {"verify_destination": False}
        raise RuntimeError("stable dispatcher repair failed")

    _prepare_worker(monkeypatch, profile, fail_repair)
    parent_handle = _exited_parent_handle(request)
    ready_handle = native_frontdoor._create_activation_ready_event()

    result = native_frontdoor._activate_managed_generation_after_parent(
        parent_handle, ready_handle, root, generation, "1.2.3"
    )

    assert result == 1
    assert (root / "runtime-selection.json").read_bytes() == previous
    assert (
        (root / "logs" / f"activation-{generation}.log")
        .read_text()
        .startswith("activation failed: RuntimeError")
    )


def test_ActivationWorker_WhenCandidateIsOlder_SkipsStableRepairAndPreservesSelection(
    monkeypatch, request, trusted_base_dir, tmp_path
):
    profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    old_generation = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    generation = "0123456789abcdef0123456789abcdef"
    for name in (old_generation, generation):
        _create_generation(root, name)
    cli_main.atomic_write_bytes_anchored(
        root / "runtime-selection.json",
        encode_selection(ManagedRuntimeSelection(old_generation, "1.3.0")),
    )
    previous = (root / "runtime-selection.json").read_bytes()
    dispatcher = b"previous stable dispatcher"
    stable_path = root / "bin" / "tg.exe"
    stable_path.parent.mkdir()
    _create_restricted_file(stable_path, dispatcher)
    _prepare_worker(
        monkeypatch,
        profile,
        lambda _version, path, **_kwargs: path.write_bytes(b"replaced dispatcher"),
    )
    parent_handle = _exited_parent_handle(request)
    ready_handle = native_frontdoor._create_activation_ready_event()

    result = native_frontdoor._activate_managed_generation_after_parent(
        parent_handle, ready_handle, root, generation, "1.2.3"
    )

    assert result == 1
    assert (root / "runtime-selection.json").read_bytes() == previous
    assert stable_path.read_bytes() == dispatcher
    assert (
        (root / "logs" / f"activation-{generation}.log")
        .read_text()
        .startswith("activation failed: RuntimeError")
    )


def test_ActivationWorker_WhenParentHandleIsInvalid_LogsFailureWithoutPublishing(
    monkeypatch, trusted_base_dir, tmp_path
):
    profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    _create_generation(root, generation)
    _prepare_worker(monkeypatch, profile, lambda _version, path: path.write_bytes(b"stable"))
    ready_handle = native_frontdoor._create_activation_ready_event()

    result = native_frontdoor._activate_managed_generation_after_parent(
        0, ready_handle, root, generation, "1.2.3"
    )

    status = root / "logs" / f"activation-{generation}.log"
    assert result == 1
    assert not (root / "runtime-selection.json").exists()
    assert status.read_text().startswith("activation failed: RuntimeError")


def test_ActivationWorker_WhenRootDiffersFromProfile_DoesNotCreateLogs(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    untrusted_root = tmp_path / "untrusted" / ".tensor-grep"
    monkeypatch.setattr(managed_runtime_candidate, "_windows_profile_directory", lambda: profile)
    result = native_frontdoor._activate_managed_generation_after_parent(
        0, 0, untrusted_root, "0123456789abcdef0123456789abcdef", "1.2.3"
    )

    assert result == 1
    assert not untrusted_root.exists()


def test_StableDispatcherMatch_WhenPinnedBytesMatchPublishedDigest_ReturnsTrue(
    monkeypatch, trusted_base_dir, tmp_path
):
    _profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    stable = root / "bin" / "tg.exe"
    stable.parent.mkdir()
    payload = b"verified stable dispatcher"
    _create_restricted_file(stable, payload)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda _version: f"{digest}  tg.exe\n",
    )
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda _version: [(types.SimpleNamespace(asset_name="tg.exe"), "unused")],
    )

    assert native_frontdoor._stable_dispatcher_matches_release("1.2.3", stable)


def test_StableDispatcherMatch_WhenStableExecutableHasHardLink_ReturnsFalse(
    monkeypatch, trusted_base_dir, tmp_path
):
    _profile, root = _trusted_install_root(trusted_base_dir, tmp_path)
    stable = root / "bin" / "tg.exe"
    stable.parent.mkdir()
    _create_restricted_file(stable, b"official bytes")
    os.link(stable, trusted_base_dir / f"linked-{tmp_path.name}.exe")
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda _version: pytest.fail("untrusted stable executable must be refused before hashing"),
    )

    assert not native_frontdoor._stable_dispatcher_matches_release("1.2.3", stable)
