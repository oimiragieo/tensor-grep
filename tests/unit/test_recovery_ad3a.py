"""Focused regression coverage for the disjoint AD3A recovery wave."""

import builtins
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap, lsp_provider_setup, mcp_server
from tensor_grep.cli import coverage as coverage_module
from tensor_grep.cli import main as cli_main
from tensor_grep.cli.formatters.table_fmt import TableFormatter
from tensor_grep.core import cross_encoder_assets, retrieval_chunker, retrieval_fusion
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.cross_encoder import apply_cross_encoder
from tensor_grep.core.hardware.memory_manager import MemoryManager
from tensor_grep.core.result import MatchLine, SearchResult
from tensor_grep.io.directory_scanner import DirectoryScanner
from tensor_grep.io.reader_fallback import FallbackReader
from tensor_grep.io.root_probe import _RG_SHORT_OPTIONS_WITH_VALUES, iter_top_level_vendored_dirs


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


def test_tg039_followed_symlinked_vendor_remains_guarded(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "needle.txt").write_text("needle\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "vendor").symlink_to(target, target_is_directory=True)

    assert bootstrap._search_args_include_unbounded_broad_scan(["needle", "--follow", str(root)])
    refused, names = cli_main._should_refuse_unbounded_vendored_root_scan(
        [str(root)],
        SearchConfig(follow=True),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )
    assert refused is True
    assert names == ["vendor"]

    result = CliRunner().invoke(cli_main.app, ["search", "needle", "--follow", str(root)])

    assert result.exit_code == 2
    assert "broad root scan refused" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_tg039_effective_rg_follow_policy_includes_config_and_cli_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    assert _RG_SHORT_OPTIONS_WITH_VALUES == {
        flag[1] for flag in bootstrap._SEARCH_ATTACHED_VALUE_SHORT_FLAGS
    }
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "vendor").symlink_to(target, target_is_directory=True)
    rg_config = tmp_path / "ripgrep.conf"
    rg_config.write_text("--follow\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(rg_config))

    assert bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert not bootstrap._search_args_include_unbounded_broad_scan([
        "needle",
        "--no-follow",
        str(root),
    ])
    assert not bootstrap._search_args_include_unbounded_broad_scan([
        "needle",
        "--no-config",
        str(root),
    ])
    assert not bootstrap._search_args_include_unbounded_broad_scan([
        "needle",
        "--follow",
        "--no-follow",
        str(root),
    ])
    assert bootstrap._search_args_include_unbounded_broad_scan([
        "needle",
        "--no-follow",
        "--follow",
        str(root),
    ])

    def refused(config: SearchConfig) -> bool:
        return cli_main._should_refuse_unbounded_vendored_root_scan(
            [str(root)],
            config,
            allow_broad_generated_scan=False,
            paths_defaulted=False,
        )[0]

    assert refused(SearchConfig())
    assert not refused(SearchConfig(no_config=True))
    assert not refused(SearchConfig(no_follow=True))
    assert not refused(SearchConfig(follow=True, no_follow=True))

    rg_config.write_text("--follow\n--no-follow\n", encoding="utf-8")
    assert not bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert not refused(SearchConfig())

    rg_config.write_text("--no-follow\n--follow\n", encoding="utf-8")
    assert bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert refused(SearchConfig())

    rg_config.write_text("--no-follow\n", encoding="utf-8")
    assert refused(SearchConfig(follow=True))

    rg_config.write_text("-Li\n", encoding="utf-8")
    assert bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert refused(SearchConfig())

    # The remainder of a value-taking short flag is data, not another flag.
    rg_config.write_text("-gL\n", encoding="utf-8")
    assert not bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert not refused(SearchConfig())


@pytest.mark.parametrize(
    ("flags", "expected_follow"),
    [
        (["--no-follow", "--follow"], True),
        (["--follow", "--no-follow"], False),
        (["--follow", "--no-follow", "--follow"], True),
        (["--no-follow", "--follow", "--no-follow"], False),
    ],
)
def test_tg039_full_cli_follow_flags_are_last_wins(
    tmp_path: Path, monkeypatch, flags: list[str], expected_follow: bool
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("needle\n", encoding="utf-8")
    seen: dict[str, SearchConfig] = {}

    def fake_passthrough(_self, _paths, _pattern, config=None):
        seen["config"] = config
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda _self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        fake_passthrough,
    )

    result = CliRunner().invoke(
        cli_main.app,
        ["search", "--passthrough", *flags, "needle", str(source)],
    )

    assert result.exit_code == 0, result.output
    config = seen["config"]
    assert config.follow is expected_follow
    assert config.no_follow is not expected_follow
    assert config.effective_rg_follow is expected_follow


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_tg039_unfollowed_windows_junction_is_not_a_vendored_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    junction = root / "vendor"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"cannot create junction on this host: {completed.stderr}")

    assert junction.is_junction()
    assert list(iter_top_level_vendored_dirs([str(root)], frozenset({"vendor"}))) == []
    assert list(
        iter_top_level_vendored_dirs([str(root)], frozenset({"vendor"}), follow_symlinks=True)
    ) == ["vendor"]
    assert not bootstrap._search_args_include_unbounded_broad_scan(["needle", str(root)])
    assert bootstrap._search_args_include_unbounded_broad_scan([
        "needle",
        "--follow",
        str(root),
    ])


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
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not destination.exists()


def test_tg046_download_success_publishes_and_cleans_staging(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "tool.bin"
    monkeypatch.setattr(
        lsp_provider_setup.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"complete payload"),
    )

    lsp_provider_setup._download("https://example.invalid/tool", destination)

    assert destination.read_bytes() == b"complete payload"
    assert list(tmp_path.glob(".tg-download-*")) == []


def test_tg046_download_never_replaces_existing_destination(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "tool.bin"
    destination.write_bytes(b"unowned replacement")
    monkeypatch.setattr(
        lsp_provider_setup.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"downloaded payload"),
    )

    with pytest.raises(FileExistsError):
        lsp_provider_setup._download("https://example.invalid/tool", destination)

    assert destination.read_bytes() == b"unowned replacement"
    assert list(tmp_path.glob(".tg-download-*")) == []


def test_tg046_download_failure_never_unlinks_close_time_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "tool.bin"
    real_close = os.close
    replacement_created = False

    def close_and_replace(descriptor: int) -> None:
        nonlocal replacement_created
        real_close(descriptor)
        if replacement_created:
            return
        destination.unlink(missing_ok=True)
        destination.write_bytes(b"unowned replacement")
        replacement_created = True

    def fail_download(*_args, **_kwargs):
        raise RuntimeError("controlled download failure")

    monkeypatch.setattr(lsp_provider_setup.os, "close", close_and_replace)
    monkeypatch.setattr(lsp_provider_setup.urllib.request, "urlopen", fail_download)

    with pytest.raises(RuntimeError, match="controlled download failure"):
        lsp_provider_setup._download("https://example.invalid/tool", destination)

    assert replacement_created
    assert destination.read_bytes() == b"unowned replacement"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd cleanup semantics")
def test_tg046_download_failure_never_unlinks_identity_check_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "tool.bin"
    replacement = b"unowned replacement"
    real_open = os.open
    real_close = os.close
    real_stat = os.stat
    real_unlink = os.unlink
    replacement_created = False

    def install_replacement() -> None:
        nonlocal replacement_created
        if replacement_created:
            return
        destination.unlink(missing_ok=True)
        destination.write_bytes(replacement)
        replacement_created = True

    def stat_then_replace(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == destination.name and kwargs.get("dir_fd") is not None:
            parent_fd = kwargs["dir_fd"]
            real_unlink(path, dir_fd=parent_fd)
            descriptor = real_open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, replacement)
            finally:
                real_close(descriptor)
            nonlocal replacement_created
            replacement_created = True
        return info

    def fail_download(*_args, **_kwargs):
        # A staging implementation never creates DESTINATION before publication, so install the
        # competing leaf here. The old direct-write implementation swaps it after its identity
        # check in stat_then_replace instead.
        if not destination.exists():
            install_replacement()
        raise RuntimeError("controlled download failure")

    monkeypatch.setattr(lsp_provider_setup.os, "stat", stat_then_replace)
    monkeypatch.setattr(lsp_provider_setup.urllib.request, "urlopen", fail_download)

    with pytest.raises(RuntimeError, match="controlled download failure"):
        lsp_provider_setup._download("https://example.invalid/tool", destination)

    assert replacement_created
    assert destination.read_bytes() == replacement


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


def test_tg052_coverage_classifies_only_expected_path_errors_as_invalid_input(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    missing = project / "missing.py"
    monkeypatch.setenv("TG_MCP_ROOT", str(project))

    missing_payload = json.loads(mcp_server.tg_coverage(str(missing)))

    assert missing_payload["error"]["code"] == "invalid_input"
    assert str(missing) not in missing_payload["error"]["message"]
    assert "internal error" not in missing_payload["error"]["message"].lower()

    blocker = project / "file.txt"
    blocker.write_text("not a directory\n", encoding="utf-8")
    not_directory_payload = json.loads(mcp_server.tg_coverage(str(blocker / "child")))

    assert not_directory_payload["error"]["code"] == "invalid_input"
    assert "internal error" not in not_directory_payload["error"]["message"].lower()

    def fail_unexpected(*_args, **_kwargs):
        raise RuntimeError("unexpected implementation failure")

    monkeypatch.setattr(coverage_module, "check_coverage", fail_unexpected)
    unexpected_payload = json.loads(mcp_server.tg_coverage(str(project)))

    assert unexpected_payload["error"]["code"] == "internal_error"

    for error_type in (FileNotFoundError, NotADirectoryError):

        def fail_nested_path(*_args, _error_type=error_type, **_kwargs):
            raise _error_type("controlled internal session/cache race")

        monkeypatch.setattr(coverage_module, "check_coverage", fail_nested_path)
        nested_payload = json.loads(mcp_server.tg_coverage(str(project)))

        assert nested_payload["error"]["code"] == "internal_error"


def test_wave6_review_hardening_follow_policy_vendored_errors_table_cap_and_impact_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import mcp_search_bounds, mcp_server
    from tensor_grep.cli.formatters.table_fmt import TableFormatter
    from tensor_grep.core.result import MatchLine, SearchResult
    from tensor_grep.io import root_probe

    # 1. Explicit no_follow short-circuits before reading RIPGREP_CONFIG_PATH
    missing_fifo_or_cfg = tmp_path / "unopened.rg"
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(missing_fifo_or_cfg))
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: (
            (_ for _ in ()).throw(AssertionError("should not stat config"))
            if self == missing_fifo_or_cfg
            else os.lstat(self)
        ),
    )
    assert root_probe.effective_rg_follow_policy(no_follow=True) is False
    assert root_probe.effective_rg_follow_policy(follow=True) is True

    # 2. Per-entry OSError on one vendored child does not hide a later vendored child
    root = tmp_path / "repo"
    (root / "build").mkdir(parents=True)
    (root / "node_modules").mkdir(parents=True)
    orig_lstat = Path.lstat

    def flaky_lstat(self: Path):
        if self.name == "build":
            raise PermissionError("access denied")
        return orig_lstat(self)

    monkeypatch.setattr(Path, "lstat", flaky_lstat)
    found = list(
        root_probe.iter_top_level_vendored_dirs([str(root)], frozenset({"build", "node_modules"}))
    )
    assert "build" in found
    assert "node_modules" in found

    # 3. _cap_output_lines preserves trailing AST omission count
    big_line = "x" * (mcp_search_bounds._MCP_MATCHES_MAX_BYTES + 100)
    omission_line = "\n... output truncated; omitted 42 matches."
    capped = mcp_search_bounds._cap_output_lines(["header", big_line, omission_line])
    assert omission_line in capped

    # 4. TableFormatter escapes CR and LF in filenames and text
    table = TableFormatter().format(
        SearchResult(matches=[MatchLine(file="a\nb\rc.py", line_number=3, text="x\ty\nz")])
    )
    assert len(table.splitlines()) == 2
    assert r"a\nb\rc.py" in table
    assert r"x\ty\nz" in table

    # 5. tg_symbol_impact clamps absurd caller max_repo_files to 50_000
    seen_limits: list[int] = []
    monkeypatch.setenv("TG_MCP_ROOT", str(root))
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_impact",
        lambda *_a, **kw: seen_limits.append(kw["max_repo_files"]) or {"ok": True},
    )
    mcp_server.tg_symbol_impact("sym", str(root), max_repo_files=9_999_999)
    assert seen_limits == [50_000]
