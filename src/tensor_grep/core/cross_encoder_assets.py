"""Revision-pinned model and CPU runtime installation, explicit and checksum-gated."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.cross_encoder_manifest import RUNTIMES

REVISION = "ce0834f22110de6d9222af7a7a03628121708969"
MODEL_BASE = f"https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/resolve/{REVISION}"
MODEL_FILES = {
    "model.onnx": ("5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a", 91011230),
    "tokenizer.json": ("d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66", 711396),
}


class CrossEncoderUnavailable(RuntimeError):
    """Assets have not been installed, or this runtime platform is unsupported."""


def default_asset_dir() -> Path:
    override = os.environ.get("TG_CROSS_ENCODER_DIR")
    return Path(override) if override else Path.home() / ".tensor-grep/models/ms-marco-MiniLM-L6-v2"


def runtime_manifest() -> dict[str, Any]:
    machine = platform.machine().lower()
    system = platform.system()
    tag = {
        ("Windows", "amd64"): "win_amd64",
        ("Windows", "arm64"): "win_arm64",
        ("Linux", "x86_64"): "x86_64",
        ("Linux", "aarch64"): "aarch64",
        ("Darwin", "arm64"): "macosx",
    }.get((system, machine))
    if tag is None:
        raise CrossEncoderUnavailable(f"no pinned CPU runtime for {system}/{machine}")
    return next(item for item in RUNTIMES if tag in item["wheel"])


def _read_verified(path: Path, sha256: str, size: int) -> bytes:
    from tensor_grep.io.confined import read_confined

    try:
        data = read_confined(path.parent, path, size)
    except FileNotFoundError as exc:
        raise CrossEncoderUnavailable(f"missing asset {path.name}") from exc
    except OSError as exc:
        raise BackendExecutionError(f"cannot read cross-encoder asset: {exc}") from exc
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise BackendExecutionError(f"cross-encoder asset checksum mismatch: {path.name}")
    return data


def verified_assets(
    root: Path, *, deadline_monotonic: float | None = None
) -> tuple[Path, Path, Path]:
    """Validate every file against hard-coded pins, including the executable library."""
    if not root.is_dir():
        raise CrossEncoderUnavailable("assets not installed")
    manifest = runtime_manifest()
    for name, (digest, size) in {**MODEL_FILES, **manifest["libraries"]}.items():
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise BackendExecutionError(
                "cross-encoder shared deadline exceeded during asset validation"
            )
        _read_verified(root / name, digest, size)
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise BackendExecutionError(
            "cross-encoder shared deadline exceeded during asset validation"
        )
    runtime = next(name for name in manifest["libraries"] if "providers_shared" not in name)
    return root / "model.onnx", root / "tokenizer.json", root / runtime


def _download(url: str, digest: str, size: int, deadline: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "tensor-grep-reranker-install"})
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackendExecutionError("cross-encoder install deadline exceeded")
    # Installation URLs and sizes are hard-coded; no caller-supplied URL or unbounded body.
    with urllib.request.urlopen(request, timeout=min(20, remaining)) as response:
        data = bytearray()
        while len(data) <= size:
            if time.monotonic() >= deadline:
                raise BackendExecutionError("cross-encoder install deadline exceeded")
            block = response.read(min(65536, size + 1 - len(data)))
            if not block:
                break
            data.extend(block)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise BackendExecutionError("cross-encoder download checksum or size mismatch")
    return bytes(data)


def fetch_cross_encoder_assets(dest_dir: Path | None = None) -> Path:
    """Install verified immutable files; reinstallation verifies an existing installation."""
    root = dest_dir if dest_dir is not None else default_asset_dir()
    manifest = runtime_manifest()
    if root.exists():
        verified_assets(root)
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 300
    try:
        with tempfile.TemporaryDirectory(prefix=".tg-cross-encoder-", dir=root.parent) as staging:
            stage = Path(staging)
            for name, (digest, size) in MODEL_FILES.items():
                remote = "onnx/model.onnx" if name == "model.onnx" else name
                (stage / name).write_bytes(
                    _download(f"{MODEL_BASE}/{remote}", digest, size, deadline)
                )
            wheel = _download(manifest["url"], manifest["sha256"], manifest["size"], deadline)
            with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
                for name, (digest, size) in manifest["libraries"].items():
                    member = archive.getinfo(f"onnxruntime/capi/{name}")
                    if member.file_size != size:
                        raise BackendExecutionError("cross-encoder runtime size mismatch")
                    # Fixed leaf names only, never extract arbitrary archive paths.
                    data = archive.read(member)
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise BackendExecutionError("cross-encoder runtime checksum mismatch")
                    (stage / name).write_bytes(data)
            verified_assets(stage)
            # Refuse replacement of existing destinations, including a racing installation.
            stage.rename(root)
    except BackendExecutionError:
        raise
    except Exception as exc:
        raise BackendExecutionError(f"cross-encoder installation failed: {exc}") from exc
    return root
