import importlib.util
import tarfile
import zipfile
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


def test_should_fail_when_uv_lock_editable_version_mismatches_expected(tmp_path):
    module = _load_module()
    module.ROOT = tmp_path

    (tmp_path / "rust_core").mkdir()
    (tmp_path / "npm").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.7.0"\n', encoding="utf-8"
    )
    (tmp_path / "rust_core" / "Cargo.toml").write_text(
        '[package]\nname = "tensor_grep_rs"\nversion = "1.7.0"\n', encoding="utf-8"
    )
    (tmp_path / "npm" / "package.json").write_text('{"version": "1.7.0"}', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "tensor-grep"\nversion = "1.6.5"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )

    errors = module.validate_release_version_parity(
        expected_version="1.7.0", check_package_managers=False
    )

    assert "uv.lock editable version 1.6.5 != expected 1.7.0" in errors


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


def test_should_retry_pypi_check_until_expected_version_becomes_visible(monkeypatch):
    module = _load_module()
    expected_version = module._version_from_pyproject()
    observed = ["0.0.1", "0.0.2", expected_version]

    def fake_fetch(*, package_name="tensor-grep"):
        return observed.pop(0)

    monkeypatch.setattr(module, "_fetch_pypi_latest", fake_fetch)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_pypi=True,
        pypi_wait_seconds=30,
        pypi_poll_interval_seconds=1,
    )
    assert errors == []


def test_should_fail_when_pypi_never_reaches_expected_version_within_wait_window(monkeypatch):
    module = _load_module()
    expected_version = module._version_from_pyproject()

    monkeypatch.setattr(module, "_fetch_pypi_latest", lambda *, package_name="tensor-grep": "0.0.1")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    ticks = iter([0.0, 0.0, 0.5, 1.1])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_pypi=True,
        pypi_wait_seconds=1,
        pypi_poll_interval_seconds=1,
    )
    assert f"pypi latest 0.0.1 != expected {expected_version}" in errors


def test_should_retry_npm_check_until_expected_version_becomes_visible(monkeypatch):
    module = _load_module()
    expected_version = module._version_from_pyproject()
    observed = ["0.0.1", "0.0.2", expected_version]

    def fake_fetch(*, package_name="tensor-grep"):
        return observed.pop(0)

    monkeypatch.setattr(module, "_fetch_npm_latest", fake_fetch)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_npm=True,
        npm_wait_seconds=30,
        npm_poll_interval_seconds=1,
    )
    assert errors == []


def test_should_fail_when_npm_never_reaches_expected_version_within_wait_window(monkeypatch):
    module = _load_module()
    expected_version = module._version_from_pyproject()

    monkeypatch.setattr(module, "_fetch_npm_latest", lambda *, package_name="tensor-grep": "0.0.1")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    ticks = iter([0.0, 0.0, 0.5, 1.1])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        check_package_managers=False,
        check_npm=True,
        npm_wait_seconds=1,
        npm_poll_interval_seconds=1,
    )
    assert f"npm latest 0.0.1 != expected {expected_version}" in errors


def test_should_fail_when_package_manager_urls_do_not_target_expected_release_version():
    module = _load_module()
    expected_version = "1.2.3"

    module._version_from_pyproject = lambda: expected_version
    module._version_from_cargo = lambda: expected_version
    module._version_from_npm = lambda: expected_version
    module._version_from_brew_formula = lambda: expected_version
    module._version_from_winget_manifest = lambda: expected_version
    module._version_from_uv_lock = lambda: expected_version

    def fake_read(path):
        path_str = str(path).replace("\\", "/")
        if path_str.endswith("scripts/tensor-grep.rb"):
            return (
                'url "https://github.com/oimiragieo/tensor-grep/releases/download/v9.9.9/tg-macos-amd64-cpu"\n'
                'url "https://github.com/oimiragieo/tensor-grep/releases/download/v9.9.9/tg-linux-amd64-cpu"\n'
            )
        if path_str.endswith("scripts/oimiragieo.tensor-grep.yaml"):
            return (
                "PackageVersion: 1.2.3\n"
                "Installers:\n"
                "  - InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v9.9.9/tg-windows-amd64-cpu.exe\n"
            )
        raise AssertionError(f"Unexpected path: {path}")

    module._read = fake_read
    errors = module.validate_release_version_parity(expected_version=expected_version)
    assert "homebrew macOS url does not target v1.2.3" in errors
    assert "homebrew Linux url does not target v1.2.3" in errors
    assert "winget installer url does not target expected release version" in errors


def test_should_accept_templated_homebrew_urls_for_expected_release_version():
    module = _load_module()
    expected_version = "1.2.3"

    module._version_from_pyproject = lambda: expected_version
    module._version_from_cargo = lambda: expected_version
    module._version_from_npm = lambda: expected_version
    module._version_from_brew_formula = lambda: expected_version
    module._version_from_winget_manifest = lambda: expected_version
    module._version_from_uv_lock = lambda: expected_version

    def fake_read(path):
        path_str = str(path).replace("\\", "/")
        if path_str.endswith("scripts/tensor-grep.rb"):
            return (
                'url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-amd64-cpu"\n'
                'url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-amd64-cpu"\n'
            )
        if path_str.endswith("scripts/oimiragieo.tensor-grep.yaml"):
            return (
                "PackageVersion: 1.2.3\n"
                "Installers:\n"
                "  - InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
            )
        raise AssertionError(f"Unexpected path: {path}")

    module._read = fake_read
    errors = module.validate_release_version_parity(expected_version=expected_version)
    assert errors == []


def test_should_fail_when_wheel_metadata_version_mismatches_expected(tmp_path):
    module = _load_module()
    expected_version = module._version_from_pyproject()

    wheel_path = tmp_path / "tensor_grep-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(
            "tensor_grep-0.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: tensor-grep\nVersion: 0.0.0\n",
        )

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        dist_dir=tmp_path,
        check_package_managers=False,
    )

    assert any("wheel metadata version 0.0.0 != expected" in err for err in errors)


def test_should_fail_when_sdist_metadata_version_mismatches_expected(tmp_path):
    module = _load_module()
    expected_version = module._version_from_pyproject()

    sdist_path = tmp_path / "tensor-grep-0.0.0.tar.gz"
    pkg_info = tmp_path / "PKG-INFO"
    pkg_info.write_text(
        "Metadata-Version: 2.1\nName: tensor-grep\nVersion: 0.0.0\n",
        encoding="utf-8",
    )
    with tarfile.open(sdist_path, "w:gz") as tf:
        tf.add(pkg_info, arcname="tensor-grep-0.0.0/PKG-INFO")

    errors = module.validate_release_version_parity(
        expected_version=expected_version,
        dist_dir=tmp_path,
        check_package_managers=False,
    )

    assert any("sdist metadata version 0.0.0 != expected" in err for err in errors)
