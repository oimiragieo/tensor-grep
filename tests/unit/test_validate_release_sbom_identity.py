from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_release_sbom_identity", ROOT / "scripts" / "validate_release_sbom_identity.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, name: str, version: str) -> None:
    path.write_text(
        json.dumps({"metadata": {"component": {"name": name, "version": version}}}),
        encoding="utf-8",
    )


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "rust": tmp_path / "sbom-rust.json",
        "python": tmp_path / "sbom-python.json",
        "cargo": tmp_path / "Cargo.toml",
        "pyproject": tmp_path / "pyproject.toml",
    }
    paths["cargo"].write_text('[package]\nname = "tensor_grep_rs"\n', encoding="utf-8")
    paths["pyproject"].write_text('[project]\nname = "tensor-grep"\n', encoding="utf-8")
    _write(paths["rust"], "tensor_grep_rs", "1.2.3")
    _write(paths["python"], "tensor-grep", "1.2.3")
    return paths


def _errors(inputs: dict[str, Path]) -> list[str]:
    return MODULE.validate_sbom_identity(
        rust_sbom=inputs["rust"],
        python_sbom=inputs["python"],
        expected_version="1.2.3",
        cargo_manifest=inputs["cargo"],
        pyproject=inputs["pyproject"],
    )


def test_validate_sbom_identity_accepts_matching_documents(inputs: dict[str, Path]) -> None:
    assert _errors(inputs) == []


@pytest.mark.parametrize(("key", "name"), [("rust", "wrong-rust"), ("python", "wrong-python")])
def test_validate_sbom_identity_rejects_wrong_project(
    inputs: dict[str, Path], key: str, name: str
) -> None:
    _write(inputs[key], name, "1.2.3")
    assert any("does not match" in error for error in _errors(inputs))


@pytest.mark.parametrize("key", ["rust", "python"])
def test_validate_sbom_identity_rejects_wrong_version(inputs: dict[str, Path], key: str) -> None:
    name = "tensor_grep_rs" if key == "rust" else "tensor-grep"
    _write(inputs[key], name, "9.9.9")
    assert any("9.9.9" in error for error in _errors(inputs))


@pytest.mark.parametrize("content", ["{", "{}", "[]"])
def test_validate_sbom_identity_rejects_malformed_or_incomplete_document(
    inputs: dict[str, Path], content: str
) -> None:
    inputs["rust"].write_text(content, encoding="utf-8")
    assert _errors(inputs)
