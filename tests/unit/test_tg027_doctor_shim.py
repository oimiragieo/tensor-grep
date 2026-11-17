import json
import shutil
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import doctor_report

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="PowerShell launcher classification"
)


def _managed_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    managed_root = home / ".tensor-grep"
    managed_bin = managed_root / "bin"
    managed_bin.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        "tensor_grep.cli.managed_runtime_candidate._windows_profile_directory", lambda: home
    )

    source_target = Path(sys.executable).with_name("tg.exe")
    assert source_target.is_file(), "canonical Windows environment must provide tg.exe"
    target = managed_bin / "tg.exe"
    shutil.copy2(source_target, target)
    (managed_bin / "tg-native-metadata.json").write_text(
        json.dumps({
            "artifact": "tensor_grep_native_frontdoor_metadata",
            "version": "1.2.3",
            "asset_flavor": "cpu",
            "requested_asset_flavor": "cpu",
            "asset_name": "tg-windows-amd64-cpu.exe",
        }),
        encoding="utf-8",
    )

    sidecar = managed_root / ".venv" / "Scripts" / "python.exe"
    managed = managed_bin / "tg.ps1"
    managed.write_text(
        "\n".join([
            '$env:PYTHONUTF8 = "1"',
            '$env:PYTHONIOENCODING = "utf-8"',
            f'$env:TG_SIDECAR_PYTHON = "{sidecar}"',
            '$env:TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR = "cpu"',
            '$env:TG_NATIVE_FRONTDOOR_FLAVOR = "cpu"',
            f'if (Test-Path -LiteralPath "{target}") {{',
            f'    $env:TG_NATIVE_TG_BINARY = "{target}"',
            f'    & "{target}" @args',
            "} else {",
            f'    & "{sidecar}" -X utf8 -m tensor_grep @args',
            "}",
            "exit $LASTEXITCODE",
            "",
        ]),
        encoding="ascii",
    )
    return home, managed, target


def test_doctor_rejects_redirected_managed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _managed, _target = _managed_layout(tmp_path, monkeypatch)
    managed_root = home / ".tensor-grep"
    external_root = tmp_path / "external-managed"
    managed_root.rename(external_root)
    try:
        managed_root.symlink_to(external_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    compat = home / "bin" / "tg.ps1"
    compat.parent.mkdir()
    external_target = external_root / "bin" / "tg.exe"
    compat.write_text(f'& "{external_target}" @args\nexit $LASTEXITCODE\n', encoding="ascii")

    assert doctor_report.powershell_shim_target(compat) is None


def test_doctor_accepts_installer_managed_and_compatibility_powershell_shims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, managed, target = _managed_layout(tmp_path, monkeypatch)
    compat = home / "bin" / "tg.ps1"
    compat.parent.mkdir()
    compat.write_text(
        f'& "{managed}" @args\nexit $LASTEXITCODE\n',
        encoding="ascii",
    )

    assert doctor_report.powershell_shim_target(managed) == target.resolve()
    assert doctor_report.powershell_shim_target(compat) == target.resolve()
    version = doctor_report._doctor_tg_candidate_version(compat)
    assert doctor_report._doctor_tg_version_looks_like_tensor_grep(version)
    assert doctor_report._doctor_tg_launcher_kind(str(compat), version) == "powershell-shim"


def test_doctor_accepts_marked_same_directory_native_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _managed, _target = _managed_layout(tmp_path, monkeypatch)
    compat_dir = home / ".local" / "bin"
    compat_dir.mkdir(parents=True)
    bridge = compat_dir / "tg.exe"
    shutil.copy2(Path(sys.executable).with_name("tg.exe"), bridge)
    (compat_dir / "tg.exe.tensor-grep-bridge").write_text(
        "tensor-grep managed tg.exe bridge\n", encoding="ascii"
    )
    shim = compat_dir / "tg.ps1"
    shim.write_text("& $PSScriptRoot\\tg.exe @args\nexit $LASTEXITCODE\n", encoding="ascii")

    assert doctor_report.powershell_shim_target(shim) == bridge.resolve()


def test_doctor_rejects_off_path_target_and_unverified_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _managed, _target = _managed_layout(tmp_path, monkeypatch)
    compat = home / "bin" / "tg.ps1"
    compat.parent.mkdir()
    off_path = tmp_path / "attacker" / "tg.exe"
    off_path.parent.mkdir()
    shutil.copy2(Path(sys.executable).with_name("tg.exe"), off_path)

    compat.write_text(f"& '{off_path}' @args\nexit $LASTEXITCODE\n", encoding="ascii")
    assert doctor_report.powershell_shim_target(compat) is None

    local_bridge = compat.parent / "tg.exe"
    shutil.copy2(Path(sys.executable).with_name("tg.exe"), local_bridge)
    compat.write_text("& $PSScriptRoot\\tg.exe @args\n", encoding="ascii")
    assert doctor_report.powershell_shim_target(compat) is None
    (compat.parent / "tg.exe.tensor-grep-bridge").write_text(
        "tensor-grep managed tg.exe bridge\n", encoding="ascii"
    )
    local_bridge.write_bytes(local_bridge.read_bytes() + b"foreign")
    assert doctor_report.powershell_shim_target(compat) is None


def test_doctor_rejects_powershell_expansion_and_extra_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, managed, _target = _managed_layout(tmp_path, monkeypatch)
    compat = home / "bin" / "tg.ps1"
    compat.parent.mkdir()
    expanded_dir = compat.parent / "$(Write-Output payload)"
    expanded_dir.mkdir()
    (expanded_dir / "tg.exe").write_bytes(b"MZ")
    compat.write_text(
        '& "$PSScriptRoot\\$(Write-Output payload)\\tg.exe" @args\nexit $LASTEXITCODE\n',
        encoding="ascii",
    )
    assert doctor_report.powershell_shim_target(compat) is None

    compat.write_text(
        f'& "{managed}" @args\nWrite-Output injected\nexit $LASTEXITCODE\n',
        encoding="ascii",
    )
    assert doctor_report.powershell_shim_target(compat) is None


def test_doctor_rejects_invalid_managed_metadata_and_oversized_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, managed, _target = _managed_layout(tmp_path, monkeypatch)
    metadata = managed.with_name("tg-native-metadata.json")
    metadata.write_text('{"artifact":"foreign"}', encoding="utf-8")
    assert doctor_report.powershell_shim_target(managed) is None
    metadata.write_bytes(b"x" * (16 * 1024 + 1))
    assert doctor_report.powershell_shim_target(managed) is None

    oversized = managed.with_name("oversized.ps1")
    oversized.write_bytes(b"#" * 4097)
    assert doctor_report.powershell_shim_target(oversized) is None


def test_doctor_candidate_version_uses_contained_bounded_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "tg.exe"
    candidate.write_bytes(b"not executable")
    seen: dict[str, object] = {}

    def fake_capture(argv, timeout_seconds, *, env=None, require_success=False):
        seen.update({
            "argv": argv,
            "timeout_seconds": timeout_seconds,
            "env": env,
            "require_success": require_success,
        })
        return b"tensor-grep 1.2.3\n", b""

    monkeypatch.setattr(doctor_report, "capture_probe", fake_capture, raising=False)
    monkeypatch.setenv("PYTHONHOME", r"C:\caller-python")
    monkeypatch.setenv("PYTHONPATH", r"C:\caller-python\Lib")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\caller-venv")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", r"C:\caller-python\python.exe")

    assert doctor_report._doctor_tg_candidate_version(candidate) == "tensor-grep 1.2.3"
    assert seen["argv"] == [str(candidate), "--version"]
    assert seen["timeout_seconds"] == 2
    assert seen["require_success"] is True
    env = seen["env"]
    assert isinstance(env, dict)
    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert "__PYVENV_LAUNCHER__" not in env
