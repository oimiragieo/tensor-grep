from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core import cross_encoder_assets as assets


def fake_assets(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    data = {"model.onnx": b"model", "tokenizer.json": b"tokenizer", "onnxruntime.dll": b"cpu"}
    pins = {name: (hashlib.sha256(body).hexdigest(), len(body)) for name, body in data.items()}
    monkeypatch.setattr(
        assets, "MODEL_FILES", {name: pins[name] for name in data if name != "onnxruntime.dll"}
    )
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("onnxruntime/capi/onnxruntime.dll", data["onnxruntime.dll"])
        archive.writestr("../../outside.txt", "must not be extracted")
    wheel_data = wheel.getvalue()
    manifest = {
        "url": "https://files.pythonhosted.org/test.whl",
        "sha256": hashlib.sha256(wheel_data).hexdigest(),
        "size": len(wheel_data),
        "libraries": {"onnxruntime.dll": pins["onnxruntime.dll"]},
    }
    monkeypatch.setattr(assets, "runtime_manifest", lambda: manifest)

    def download(url: str, digest: str, size: int, deadline: float) -> bytes:
        body = wheel_data if url.endswith(".whl") else data[url.rsplit("/", 1)[-1]]
        assert hashlib.sha256(body).hexdigest() == digest
        assert len(body) == size
        return body

    monkeypatch.setattr(assets, "_download", download)
    return data


def test_installer_extracts_only_pinned_leaf_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = fake_assets(monkeypatch)
    dest = tmp_path / "assets"
    assert assets.fetch_cross_encoder_assets(dest) == dest
    assert {p.name for p in dest.iterdir()} == set(data)
    assert not (tmp_path / "outside.txt").exists()
    assert not list(tmp_path.glob(".tg-cross-encoder-*"))
    assert assets.verified_assets(dest) == (
        dest / "model.onnx",
        dest / "tokenizer.json",
        dest / "onnxruntime.dll",
    )


def test_reinstall_verifies_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_assets(monkeypatch)
    dest = assets.fetch_cross_encoder_assets(tmp_path / "assets")

    def refused(*args: object) -> bytes:
        raise AssertionError("reinstall must verify existing assets without a download")

    monkeypatch.setattr(assets, "_download", refused)
    assert assets.fetch_cross_encoder_assets(dest) == dest
    (dest / "onnxruntime.dll").write_bytes(b"bad")
    with pytest.raises(BackendExecutionError, match="checksum mismatch"):
        assets.fetch_cross_encoder_assets(dest)


def test_fetch_failure_discards_partial_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_assets(monkeypatch)

    def fail(*args: object) -> bytes:
        raise OSError("connection refused")

    monkeypatch.setattr(assets, "_download", fail)
    with pytest.raises(BackendExecutionError, match="connection refused"):
        assets.fetch_cross_encoder_assets(tmp_path / "assets")
    assert not list(tmp_path.iterdir())


def test_racing_empty_destination_is_preserved_without_publishing_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_assets(monkeypatch)
    dest = tmp_path / "assets"
    publish = assets.publish_directory_no_replace
    identities = []

    def race(stage: Path, destination: Path) -> None:
        destination.mkdir()
        identities.append((destination.stat().st_dev, destination.stat().st_ino))
        publish(stage, destination)

    monkeypatch.setattr(assets, "publish_directory_no_replace", race)
    with pytest.raises(BackendExecutionError, match="installation failed"):
        assets.fetch_cross_encoder_assets(dest)
    assert identities == [(dest.stat().st_dev, dest.stat().st_ino)]
    assert not list(dest.iterdir())
    assert not list(tmp_path.glob(".tg-cross-encoder-*"))


def test_publication_unavailable_discards_verified_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_assets(monkeypatch)

    def unavailable(*args: object) -> None:
        raise OSError("atomic no-replace directory publication unavailable")

    monkeypatch.setattr(assets, "publish_directory_no_replace", unavailable)
    with pytest.raises(BackendExecutionError, match="publication unavailable"):
        assets.fetch_cross_encoder_assets(tmp_path / "assets")
    assert not list(tmp_path.iterdir())


def test_nul_installation_path_refused_before_download_or_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_assets(monkeypatch)
    monkeypatch.setattr(assets, "_download", lambda *args: pytest.fail("invalid path downloaded"))
    with pytest.raises(BackendExecutionError, match="embedded NUL path"):
        assets.fetch_cross_encoder_assets(tmp_path / "new-parent" / "bad\x00name")
    assert not list(tmp_path.iterdir())


def test_download_size_checksum_deadline_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    class Response(io.BytesIO):
        pass

    good = b"positive control"
    digest = hashlib.sha256(good).hexdigest()
    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *a, **kw: Response(good))
    assert assets._download("https://example.com", digest, len(good), time.monotonic() + 10) == good
    with pytest.raises(BackendExecutionError, match="size mismatch"):
        assets._download("https://example.com", digest, len(good) - 1, time.monotonic() + 10)
    with pytest.raises(BackendExecutionError, match="size mismatch"):
        assets._download("https://example.com", "0" * 64, len(good), time.monotonic() + 10)
    with pytest.raises(BackendExecutionError, match="deadline"):
        assets._download("https://example.com", digest, len(good), time.monotonic() - 1)


def test_platform_manifest_is_explicit_and_unsupported_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assets.platform, "system", lambda: "Windows")
    monkeypatch.setattr(assets.platform, "machine", lambda: "AMD64")
    assert assets.runtime_manifest()["wheel"].endswith("win_amd64.whl")
    monkeypatch.setattr(assets.platform, "machine", lambda: "unknown")
    with pytest.raises(assets.CrossEncoderUnavailable, match="no pinned CPU runtime"):
        assets.runtime_manifest()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file control")
def test_fifo_asset_refused_before_open(tmp_path: Path) -> None:
    fifo = tmp_path / "model.onnx"
    os.mkfifo(fifo)
    with pytest.raises(BackendExecutionError, match="non-regular"):
        assets._read_verified(fifo, "0" * 64, 32)


def test_asset_validation_honors_shared_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    fake_assets(monkeypatch)
    dest = assets.fetch_cross_encoder_assets(tmp_path / "assets")
    assets.verified_assets(dest, deadline_monotonic=time.monotonic() + 30)
    with pytest.raises(BackendExecutionError, match="shared deadline"):
        assets.verified_assets(dest, deadline_monotonic=time.monotonic() - 1)
