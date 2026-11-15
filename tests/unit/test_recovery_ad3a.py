"""Focused regression coverage for the disjoint AD3A recovery wave."""

import builtins
import os
from pathlib import Path

import pytest

from tensor_grep.cli import lsp_provider_setup
from tensor_grep.cli.formatters.table_fmt import TableFormatter
from tensor_grep.core import cross_encoder_assets, retrieval_chunker, retrieval_fusion
from tensor_grep.core.cross_encoder import apply_cross_encoder
from tensor_grep.core.hardware.memory_manager import MemoryManager
from tensor_grep.core.result import MatchLine, SearchResult
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


def test_tg048_table_formatter_escapes_tabs_in_cells() -> None:
    result = SearchResult(
        matches=[MatchLine(line_number=7, file="dir\tname.py", text="needle\ttext")]
    )

    row = TableFormatter().format(result).splitlines()[1]

    assert row.split("\t") == [r"dir\tname.py", "7", r"needle\ttext"]


def test_tg049_mts_and_cts_use_typescript_structural_parser(monkeypatch) -> None:
    parser = object()
    tsx_modes: list[bool] = []

    def fake_typescript_parser(*, tsx: bool):
        tsx_modes.append(tsx)
        return parser

    monkeypatch.setattr(retrieval_chunker, "_structural_typescript_parser", fake_typescript_parser)

    assert retrieval_chunker._structural_parser_for_path("module.mts") is parser
    assert retrieval_chunker._structural_parser_for_path("module.cts") is parser
    assert tsx_modes == [False, False]


def test_tg050_cross_encoder_uses_shared_rrf_constant(monkeypatch) -> None:
    import tensor_grep

    class FakeRustCore:
        @staticmethod
        def cross_encoder_scores(*_args):
            return [0.0, 3.0, 4.0, 2.0]

    monkeypatch.setattr(retrieval_fusion, "DEFAULT_K", 1)
    monkeypatch.setattr(tensor_grep, "rust_core", FakeRustCore, raising=False)
    monkeypatch.setattr(
        cross_encoder_assets,
        "scorer_assets",
        lambda _root, **_kwargs: (Path("model"), Path("tokenizer"), Path("runtime")),
    )
    result = SearchResult(
        matches=[MatchLine(line_number=i + 1, file=f"file{i}", text=f"text{i}") for i in range(4)]
    )

    apply_cross_encoder(result, "query", "cross-encoder")

    assert [match.text for match in result.matches] == ["text2", "text0", "text1", "text3"]
    assert result.rank_fusion["cross_encoder"]["rrf_k"] == 1
