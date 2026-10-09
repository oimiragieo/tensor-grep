from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tensor_grep.cli.checkout_environment import diagnose_checkout_environment


def _environment(
    root: Path, *, layout: str = "Lib", version: str = "1.0.0", origin: Path | None = None
) -> Path:
    (root / "pyproject.toml").write_text(
        '[project]\nname="tensor-grep"\nversion="2.0.0"\n', encoding="utf-8"
    )
    site = root / ".venv" / layout / "site-packages"
    metadata = site / "tensor_grep-1.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(f"Name: tensor-grep\nVersion: {version}\n", encoding="utf-8")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": (origin or root).as_uri(), "dir_info": {"editable": True}}),
        encoding="utf-8",
    )
    return metadata


@pytest.mark.parametrize("layout", ["Lib", "lib/python3.12"])
def test_stale_environment_requires_matching_editable_provenance(
    tmp_path: Path, layout: str
) -> None:
    _environment(tmp_path, layout=layout)
    result = diagnose_checkout_environment(tmp_path)
    assert result["status"] == "stale_editable"
    assert "uv run --refresh-package tensor-grep tg --version" in result["remediation"]
    assert result["read_only"] is True


def test_unrelated_environment_never_labelled_stale(tmp_path: Path) -> None:
    _environment(tmp_path, origin=tmp_path.parent)
    assert diagnose_checkout_environment(tmp_path)["status"] == "unrelated_environment"


def test_missing_direct_url_is_unverified(tmp_path: Path) -> None:
    metadata = _environment(tmp_path)
    (metadata / "direct_url.json").unlink()
    assert diagnose_checkout_environment(tmp_path)["status"] == "unverified_provenance"


def test_current_environment_positive_control(tmp_path: Path) -> None:
    _environment(tmp_path, version="2.0.0")
    assert diagnose_checkout_environment(tmp_path)["status"] == "current"


def test_ambiguous_and_bounded_metadata(tmp_path: Path) -> None:
    metadata = _environment(tmp_path)
    (metadata / "METADATA").write_bytes(b"x" * (1024 * 1024 + 1))
    result = diagnose_checkout_environment(tmp_path)
    assert result["status"] == "ambiguous_environment"
    assert "1 MiB" in result["error"]


def test_diagnosis_does_not_execute_checkout_interpreter(tmp_path: Path) -> None:
    _environment(tmp_path)
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir()
    interpreter.write_bytes(b"this is deliberately not executable")
    assert diagnose_checkout_environment(tmp_path)["status"] == "stale_editable"


def test_distinct_missing_project_environment_and_package(tmp_path: Path) -> None:
    assert diagnose_checkout_environment(tmp_path)["status"] == "missing_project"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="tensor-grep"\nversion="1.0.0"\n', encoding="utf-8"
    )
    assert diagnose_checkout_environment(tmp_path)["status"] == "missing_environment"
    (tmp_path / ".venv").mkdir()
    assert diagnose_checkout_environment(tmp_path)["status"] == "missing_package"


def test_metadata_name_must_match_package(tmp_path: Path) -> None:
    metadata = _environment(tmp_path)
    (metadata / "METADATA").write_text(
        "Name: unrelated-package\nVersion: 1.0.0\n", encoding="utf-8"
    )
    assert diagnose_checkout_environment(tmp_path)["status"] == "unrelated_environment"


@pytest.mark.parametrize("version", ["", "not-a-version"])
def test_invalid_metadata_version_is_ambiguous(tmp_path: Path, version: str) -> None:
    _environment(tmp_path, version=version)
    assert diagnose_checkout_environment(tmp_path)["status"] == "ambiguous_environment"


def test_missing_source_version_is_ambiguous(tmp_path: Path) -> None:
    _environment(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="tensor-grep"\n', encoding="utf-8")
    assert diagnose_checkout_environment(tmp_path)["status"] == "ambiguous_environment"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file control")
def test_environment_fifo_metadata_refused_without_read(tmp_path: Path) -> None:
    metadata = _environment(tmp_path)
    target = metadata / "METADATA"
    target.unlink()
    os.mkfifo(target)  # type: ignore[attr-defined]
    result = diagnose_checkout_environment(tmp_path)
    assert result["status"] == "ambiguous_environment"
    assert "non-regular" in result["error"]
