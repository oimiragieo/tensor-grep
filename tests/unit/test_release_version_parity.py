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
    expected_version = module._version_from_pyproject()
    errors = module.validate_release_version_parity(expected_version=expected_version)
    assert errors == []


def test_should_fail_when_expected_version_does_not_match_project_versions():
    module = _load_module()
    errors = module.validate_release_version_parity(expected_version="9.9.9")
    assert any("pyproject version" in err for err in errors)
    assert any("cargo version" in err for err in errors)
    assert any("npm version" in err for err in errors)


def test_should_fail_when_expected_tag_mismatches_expected_version():
    module = _load_module()
    expected_version = module._version_from_pyproject()
    wrong_tag = f"v{expected_version}.x"
    errors = module.validate_release_version_parity(
        expected_version=expected_version, expected_tag=wrong_tag
    )
    assert f"expected tag {wrong_tag} != v{expected_version}" in errors


def test_should_skip_package_manager_checks_when_requested():
    module = _load_module()
    errors = module.validate_release_version_parity(
        expected_version="9.9.9", check_package_managers=False
    )
    assert any("pyproject version" in err for err in errors)
    assert all("homebrew" not in err for err in errors)
    assert all("winget" not in err for err in errors)


def test_should_retry_pypi_check_until_expected_version_becomes_visible():
    module = _load_module()
    expected_version = module._version_from_pyproject()
    observed = ["0.0.1", "0.0.2", expected_version]

    def fake_fetch(*, package_name="tensor-grep"):
        return observed.pop(0)

    module._fetch_pypi_latest = fake_fetch
    module.time.sleep = lambda _seconds: None

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_pypi=True,
        pypi_wait_seconds=30,
        pypi_poll_interval_seconds=1,
    )
    assert errors == []


def test_should_fail_when_pypi_never_reaches_expected_version_within_wait_window():
    module = _load_module()
    expected_version = module._version_from_pyproject()

    module._fetch_pypi_latest = lambda *, package_name="tensor-grep": "0.0.1"
    module.time.sleep = lambda _seconds: None

    ticks = iter([0.0, 0.0, 0.5, 1.1])
    module.time.monotonic = lambda: next(ticks)

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_pypi=True,
        pypi_wait_seconds=1,
        pypi_poll_interval_seconds=1,
    )
    assert f"pypi latest 0.0.1 != expected {expected_version}" in errors
