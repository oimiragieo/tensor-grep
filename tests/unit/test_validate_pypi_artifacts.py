from __future__ import annotations

import importlib.util
import tarfile
import zipfile
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_pypi_artifacts.py"
    spec = importlib.util.spec_from_file_location("validate_pypi_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_wheel(
    path: Path, version: str, tag: str, *, include_console_script: bool = True
) -> Path:
    wheel_name = f"tensor_grep-{version}-cp311-abi3-{tag}.whl"
    wheel_path = path / wheel_name
    dist_info = f"tensor_grep-{version}.dist-info/METADATA"
    entry_points = f"tensor_grep-{version}.dist-info/entry_points.txt"
    metadata = f"Metadata-Version: 2.1\nName: tensor-grep\nVersion: {version}\n"
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(dist_info, metadata)
        if include_console_script:
            zf.writestr(
                entry_points,
                "[console_scripts]\ntg = tensor_grep.cli.main:app\n",
            )
    return wheel_path


def _write_sdist(path: Path, version: str) -> Path:
    sdist_name = f"tensor_grep-{version}.tar.gz"
    sdist_path = path / sdist_name
    pkg_info_path = f"tensor_grep-{version}/PKG-INFO"
    pkg_info = f"Metadata-Version: 2.1\nName: tensor-grep\nVersion: {version}\n"
    with tarfile.open(sdist_path, "w:gz") as tf:
        info = tarfile.TarInfo(pkg_info_path)
        data = pkg_info.encode("utf-8")
        info.size = len(data)
        import io

        tf.addfile(info, io.BytesIO(data))
    return sdist_path


def test_should_validate_matching_artifacts(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_wheel(tmp_path, version, "win_amd64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert errors == []


def test_should_fail_when_wheel_version_mismatches(tmp_path: Path):
    module = _load_module()
    _write_wheel(tmp_path, "0.11.0", "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, "0.11.1", "macosx_11_0_x86_64")
    _write_wheel(tmp_path, "0.11.1", "win_amd64")
    _write_sdist(tmp_path, "0.11.1")

    errors = module.validate(
        dist_dir=tmp_path,
        version="0.11.1",
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("Wheel filename version mismatch" in err for err in errors)


def test_should_fail_when_required_platform_wheel_missing(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("Missing required wheel platform artifact: windows" == err for err in errors)


def test_should_build_hash_matrix_for_all_artifacts(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    wheel = _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    sdist = _write_sdist(tmp_path, version)

    matrix = module.build_hash_matrix(tmp_path)

    assert set(matrix.keys()) == {wheel.name, sdist.name}
    for digest in matrix.values():
        assert len(digest) == 64


def test_should_fail_when_wheel_missing_tg_console_script(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64", include_console_script=False)
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_wheel(tmp_path, version, "win_amd64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("missing tg console script entry point" in err for err in errors)
