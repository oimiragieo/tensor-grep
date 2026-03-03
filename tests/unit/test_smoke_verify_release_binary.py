from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "smoke_verify_release_binary.py"
    spec = importlib.util.spec_from_file_location("smoke_verify_release_binary", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_verify_linux_binary_version_output(tmp_path: Path):
    module = _load_module()
    binary = tmp_path / "binary-Linux-cpu" / "tg-linux-amd64-cpu"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"#!/bin/sh\n")

    with patch.object(module.subprocess, "run") as mock_run:
        mock_run.return_value.stdout = "tensor-grep 1.2.3\n"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        errors = module.smoke_verify_linux_binary(
            artifacts_dir=tmp_path,
            expected_version="1.2.3",
        )

    assert errors == []


def test_should_fail_when_linux_binary_missing(tmp_path: Path):
    module = _load_module()
    errors = module.smoke_verify_linux_binary(artifacts_dir=tmp_path, expected_version="1.2.3")
    assert any("Missing Linux CPU release binary" in err for err in errors)


def test_should_fail_when_version_output_mismatch(tmp_path: Path):
    module = _load_module()
    binary = tmp_path / "binary-Linux-cpu" / "tg-linux-amd64-cpu"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"#!/bin/sh\n")

    with patch.object(module.subprocess, "run") as mock_run:
        mock_run.return_value.stdout = "tensor-grep 1.2.2\n"
        mock_run.return_value.stderr = ""
        mock_run.return_value.returncode = 0

        errors = module.smoke_verify_linux_binary(
            artifacts_dir=tmp_path,
            expected_version="1.2.3",
        )

    assert any("Version output mismatch" in err for err in errors)
