import os
import sys
from contextlib import ExitStack

import pytest

from tensor_grep.cli import session_daemon_trust, session_daemon_winsec
from tensor_grep.cli._index_lock import index_lock
from tensor_grep.cli.managed_runtime_reader import read_managed_runtime
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, encode_selection
from tensor_grep.cli.native_frontdoor import (
    _pin_managed_directory,
    _pin_managed_generation_executables,
    _publish_managed_generation,
    selected_managed_windows_version,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="managed selection is Windows-only")


def _managed_root(trusted_base_dir, tmp_path):
    root = trusted_base_dir / f"managed-selection-version-{tmp_path.name}"
    assert session_daemon_trust._ensure_secret_dir(root)
    assert session_daemon_trust._parent_trusted(root)
    return root


def _write_selection(root, generation, version):
    path = root / "runtime-selection.json"
    sid = session_daemon_winsec.current_user_sid()
    assert sid is not None
    handle = session_daemon_winsec.create_new_restricted(str(path), sid)
    assert handle is not None
    try:
        assert session_daemon_winsec.write_all(
            handle,
            encode_selection(ManagedRuntimeSelection(generation=generation, version=version)),
        )
    finally:
        session_daemon_winsec.close_handle(handle)


def _write_raw_selection(root, payload):
    path = root / "runtime-selection.json"
    sid = session_daemon_winsec.current_user_sid()
    assert sid is not None
    handle = session_daemon_winsec.create_new_restricted(str(path), sid)
    assert handle is not None
    try:
        assert session_daemon_winsec.write_all(handle, payload)
    finally:
        session_daemon_winsec.close_handle(handle)


def _create_generation(root, generation):
    generation_root = root / "runtimes" / generation
    (generation_root / "bin").mkdir(parents=True)
    (generation_root / ".venv" / "Scripts").mkdir(parents=True)
    (generation_root / "bin" / "tg.exe").write_bytes(b"native")
    (generation_root / ".venv" / "Scripts" / "python.exe").write_bytes(b"python")
    return generation_root


def test_SelectedManagedVersion_WhenNoSelectionExists_ReturnsNone(trusted_base_dir, tmp_path):
    root = _managed_root(trusted_base_dir, tmp_path)

    assert selected_managed_windows_version(root) is None


def test_SelectedManagedVersion_WhenSelectedGenerationIsComplete_ReturnsItsVersion(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    generation_root = root / "runtimes" / generation
    (generation_root / "bin").mkdir(parents=True)
    (generation_root / ".venv" / "Scripts").mkdir(parents=True)
    (generation_root / "bin" / "tg.exe").write_bytes(b"native")
    (generation_root / ".venv" / "Scripts" / "python.exe").write_bytes(b"python")
    _write_selection(root, generation, "1.2.3")

    assert selected_managed_windows_version(root) == "1.2.3"


def test_SelectedManagedVersion_WhenSelectedGenerationLacksAnExecutable_RefusesSelection(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    _write_selection(root, generation, "1.2.3")

    with pytest.raises(RuntimeError, match="incomplete or untrusted generation"):
        selected_managed_windows_version(root)


def test_SelectedManagedVersion_WhenSelectorIsMalformed_RefusesSelection(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    _write_raw_selection(root, b"{}")

    with pytest.raises(RuntimeError, match="selector cannot be trusted"):
        selected_managed_windows_version(root)


def test_SelectedManagedVersion_WhenSelectedNativeIsHardLinked_RefusesGeneration(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    generation_root = root / "runtimes" / generation
    (generation_root / "bin").mkdir(parents=True)
    (generation_root / ".venv" / "Scripts").mkdir(parents=True)
    external_native = trusted_base_dir / f"outside-{tmp_path.name}.exe"
    native = generation_root / "bin" / "tg.exe"
    sid = session_daemon_winsec.current_user_sid()
    assert sid is not None
    native_handle = session_daemon_winsec.create_new_restricted(str(native), sid)
    assert native_handle is not None
    try:
        assert session_daemon_winsec.write_all(native_handle, b"native")
    finally:
        session_daemon_winsec.close_handle(native_handle)
    os.link(native, external_native)
    (generation_root / ".venv" / "Scripts" / "python.exe").write_bytes(b"python")
    _write_selection(root, generation, "1.2.3")

    native_handle = session_daemon_winsec.open_no_follow(str(native), share=3)
    assert native_handle is not None
    try:
        assert session_daemon_trust._windows_handle_trusted(native_handle, check_dacl=True)
        assert session_daemon_winsec.link_count(native_handle) == 2
    finally:
        session_daemon_winsec.close_handle(native_handle)

    with pytest.raises(RuntimeError, match="incomplete or untrusted generation"):
        selected_managed_windows_version(root)


def test_ManagedSelectionPublication_WhenCandidateIsVerified_SelectsGeneration(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    generation = "0123456789abcdef0123456789abcdef"
    generation_root = _create_generation(root, generation)
    (root / "runtimes").mkdir(exist_ok=True)

    with ExitStack() as held:
        _pin_managed_directory(root, held)
        _pin_managed_directory(root / "runtimes", held)
        _pin_managed_directory(generation_root, held)
        _pin_managed_generation_executables(generation_root, held)
        with index_lock(root / "runtime-selection.json"):
            _publish_managed_generation(root, generation, "1.2.3", held)

    selected = read_managed_runtime(trusted_root=root)
    assert selected is not None
    assert selected.selection == ManagedRuntimeSelection(generation, "1.2.3")
    assert list(root.glob("runtime-selection.json.*.tmp")) == []


def test_ManagedSelectionPublication_WhenCandidateIsOlder_PreservesNewerSelection(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    selected_generation = "0123456789abcdef0123456789abcdef"
    candidate_generation = "fedcba9876543210fedcba9876543210"
    _create_generation(root, selected_generation)
    candidate_root = _create_generation(root, candidate_generation)
    _write_selection(root, selected_generation, "1.3.0")
    original = (root / "runtime-selection.json").read_bytes()
    attempted = [False]

    with ExitStack() as held:
        _pin_managed_directory(root, held)
        _pin_managed_directory(root / "runtimes", held)
        _pin_managed_directory(candidate_root, held)
        _pin_managed_generation_executables(candidate_root, held)
        with index_lock(root / "runtime-selection.json"):
            with pytest.raises(RuntimeError, match="refusing to activate older candidate"):
                _publish_managed_generation(root, candidate_generation, "1.2.9", held, attempted)

    assert (root / "runtime-selection.json").read_bytes() == original
    assert attempted == [False]
    assert candidate_root.is_dir()
    assert list(root.glob("runtime-selection.json.*.tmp")) == []


def test_ManagedSelectionPublication_WhenSelectorIsLocked_PreservesOldRecordAndCleansTemp(
    trusted_base_dir, tmp_path
):
    root = _managed_root(trusted_base_dir, tmp_path)
    selected_generation = "0123456789abcdef0123456789abcdef"
    candidate_generation = "fedcba9876543210fedcba9876543210"
    _create_generation(root, selected_generation)
    candidate_root = _create_generation(root, candidate_generation)
    _write_selection(root, selected_generation, "1.2.3")
    original = (root / "runtime-selection.json").read_bytes()
    blocker = session_daemon_winsec.open_no_follow(str(root / "runtime-selection.json"), share=3)
    assert blocker is not None

    try:
        with ExitStack() as held:
            _pin_managed_directory(root, held)
            _pin_managed_directory(root / "runtimes", held)
            _pin_managed_directory(candidate_root, held)
            _pin_managed_generation_executables(candidate_root, held)
            with index_lock(root / "runtime-selection.json"):
                with pytest.raises(OSError):
                    _publish_managed_generation(root, candidate_generation, "1.2.4", held)
    finally:
        session_daemon_winsec.close_handle(blocker)

    assert (root / "runtime-selection.json").read_bytes() == original
    assert list(root.glob("runtime-selection.json.*.tmp")) == []


@pytest.mark.parametrize(
    ("generation", "version"),
    [("bad-generation", "1.2.3"), ("0123456789abcdef0123456789abcdef", "latest")],
)
def test_ManagedSelectionPublication_WhenRecordIsInvalid_RefusesBeforeTemporaryCreation(
    trusted_base_dir, tmp_path, generation, version
):
    root = _managed_root(trusted_base_dir, tmp_path)
    (root / "runtimes").mkdir()

    with ExitStack() as held, pytest.raises(ValueError, match="selection"):
        _publish_managed_generation(root, generation, version, held)

    assert list(root.iterdir()) == [root / "runtimes"]
    assert list(root.glob("runtime-selection.json.*.tmp")) == []
