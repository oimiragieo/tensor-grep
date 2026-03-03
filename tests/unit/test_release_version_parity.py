import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_version_parity.py"
    spec = importlib.util.spec_from_file_location("validate_release_version_parity", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_validate_release_version_parity_without_pypi():
    module = _load_module()
    errors = module.validate_release_version_parity(expected_version="0.14.1")
    assert errors == []


def test_should_fail_when_expected_version_does_not_match_project_versions():
    module = _load_module()
    errors = module.validate_release_version_parity(expected_version="9.9.9")
    assert any("pyproject version" in err for err in errors)
    assert any("cargo version" in err for err in errors)
    assert any("npm version" in err for err in errors)


def test_should_fail_when_expected_tag_mismatches_expected_version():
    module = _load_module()
    errors = module.validate_release_version_parity(
        expected_version="0.14.1", expected_tag="v0.14.2"
    )
    assert "expected tag v0.14.2 != v0.14.1" in errors


def test_should_skip_package_manager_checks_when_requested():
    module = _load_module()
    errors = module.validate_release_version_parity(
        expected_version="9.9.9", check_package_managers=False
    )
    assert any("pyproject version" in err for err in errors)
    assert all("homebrew" not in err for err in errors)
    assert all("winget" not in err for err in errors)
