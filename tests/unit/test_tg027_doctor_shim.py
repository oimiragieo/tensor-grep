import sys
from pathlib import Path

import pytest

from tensor_grep.cli import doctor_report


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher classification")
def test_doctor_probes_owned_powershell_launcher_target(tmp_path: Path) -> None:
    target = Path(sys.executable).with_name("tg.exe")
    assert target.is_file(), "canonical Windows environment must provide the tg console launcher"
    healthy = tmp_path / "healthy" / "tg.ps1"
    healthy.parent.mkdir()
    healthy.write_text(f"& '{target}' @args\nexit $LASTEXITCODE\n", encoding="utf-8")
    broken = tmp_path / "broken" / "tg.ps1"
    broken.parent.mkdir()
    broken.write_text("& './missing-target.exe' @args\nexit 1\n", encoding="utf-8")
    oversized = tmp_path / "oversized.ps1"
    oversized.write_bytes(b"#" * 4097)

    version = doctor_report._doctor_tg_candidate_version(healthy)

    assert doctor_report._doctor_tg_version_looks_like_tensor_grep(version)
    assert doctor_report._doctor_tg_launcher_kind(str(healthy), version) == "powershell-shim"
    assert doctor_report._doctor_tg_candidate_version(broken) is None
    assert doctor_report._doctor_tg_launcher_kind(str(broken), None) == "foreign"
    assert doctor_report.powershell_shim_target(oversized) is None
