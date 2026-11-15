"""Focused regression coverage for the disjoint AD3A recovery wave."""

import builtins
import os
from pathlib import Path

import pytest

from tensor_grep.cli import lsp_provider_setup
from tensor_grep.core.hardware.memory_manager import MemoryManager
from tensor_grep.io.directory_scanner import DirectoryScanner
from tensor_grep.io.reader_fallback import FallbackReader
from tensor_grep.io.root_probe import iter_top_level_vendored_dirs


def test_tg037_gitignore_directory_does_not_abort_scan(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").mkdir()
    source = tmp_path / "keep.py"
    source.write_text("needle\n", encoding="utf-8")

    scanner = DirectoryScanner()

    assert list(scanner.walk(str(tmp_path))) == [str(source)]


def test_tg038_fallback_reader_ignores_directory_input(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file.log"
    directory.mkdir()

    assert list(FallbackReader().read_lines(str(directory))) == []


def test_tg039_symlinked_vendor_is_not_a_vendored_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    link = root / "vendor"
    link.symlink_to(target, target_is_directory=True)

    assert list(iter_top_level_vendored_dirs([str(root)], frozenset({"vendor"}))) == []


def test_tg043_missing_psutil_uses_safe_cpu_fallback(monkeypatch) -> None:
    manager = MemoryManager()
    monkeypatch.setattr(manager, "get_vram_budget_mb", lambda _device_id=0: 0)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    real_import = builtins.__import__

    def block_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ModuleNotFoundError("controlled missing psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_psutil)

    assert manager.get_recommended_chunk_size_mb() == 819


def test_tg046_download_failure_closes_destination_fd(tmp_path: Path, monkeypatch) -> None:
    opened: list[int] = []
    real_open = os.open

    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def fail_download(*_args, **_kwargs):
        raise RuntimeError("controlled download failure")

    monkeypatch.setattr(lsp_provider_setup.os, "open", tracking_open)
    monkeypatch.setattr(lsp_provider_setup.urllib.request, "urlopen", fail_download)
    destination = tmp_path / "tool.bin"

    with pytest.raises(RuntimeError, match="controlled download failure"):
        lsp_provider_setup._download("https://example.invalid/tool", destination)

    assert opened
    with pytest.raises(OSError):
        os.fstat(opened[0])
    assert not destination.exists()
