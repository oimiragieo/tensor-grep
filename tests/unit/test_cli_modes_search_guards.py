import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.completion import get_completion_script
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli.main import (
    _should_refuse_unbounded_generated_scan,
    _should_refuse_unbounded_large_root_scan,
    _should_refuse_unbounded_vendored_root_scan,
    _should_refuse_unbounded_workspace_root_scan,
    app,
)
from tensor_grep.cli.scan_guardrails import find_broad_scan_refusal
from tensor_grep.core.config import SearchConfig
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_files_mode_lists_candidates(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "x", ".", "--files"])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]


def test_files_mode_lists_candidates_without_pattern(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", "."])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]


def test_ripgrep_backend_builds_regexp_patterns_as_e_options(monkeypatch):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")

    cmd = RipgrepBackend()._build_cmd(
        file_path=["."],
        pattern="-needle",
        config=SearchConfig(regexp=["-needle", "plain"], sort_by="path", line_number=None),
        json_mode=False,
    )

    pattern_index = cmd.index("-needle")
    assert cmd[pattern_index - 1 : pattern_index + 3] == ["-e", "-needle", "-e", "plain"]
    assert cmd[-1] == "."


def test_files_mode_refuses_unbounded_broad_generated_root_scan(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--files", str(tmp_path), "--hidden"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "node_modules" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output
    assert "For bounded output:" in result.output
    assert "tg search --files <path> --hidden --max-depth" in result.output
    assert "For intentional broad scans:" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_plain_search_refuses_unbounded_multi_project_workspace_root(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name, marker_name in (
        ("alpha", "pyproject.toml"),
        ("beta", "package.json"),
        ("gamma", "Cargo.toml"),
    ):
        project = workspace / project_name
        (project / "src").mkdir(parents=True)
        (project / marker_name).write_text("", encoding="utf-8")
        (project / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "needle", str(workspace)])

    assert result.exit_code == 2
    assert "broad workspace-root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "gamma" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_files_mode_refuses_generated_root_before_rg_passthrough(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        lambda self, paths, pattern, config=None: pytest.fail(
            "generated-root guard should run before rg passthrough"
        ),
    )

    result = CliRunner().invoke(app, ["search", "--files", str(tmp_path), "--hidden"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output


def test_files_mode_json_does_not_passthrough_to_rg(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        lambda self, paths, pattern, config=None: pytest.fail(
            "--files --json should keep tensor-grep files-mode semantics"
        ),
    )

    result = CliRunner().invoke(app, ["search", "--files", "--json", str(tmp_path)])

    assert result.exit_code == 0
    assert "app.py" in result.stdout


def test_files_mode_refuses_cwd_generated_root_scan(monkeypatch, tmp_path: Path):
    venv_root = tmp_path / ".venv"
    package_dir = venv_root / "Lib" / "site-packages" / "pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "module.py").write_text("print('dep')\n", encoding="utf-8")
    monkeypatch.chdir(venv_root)

    result = CliRunner().invoke(app, ["search", "--files", ".", "--hidden", "--no-ignore"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output
    assert ".venv" in result.output


def test_files_mode_allows_bounded_broad_generated_root_scan(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    result = CliRunner().invoke(
        app,
        ["search", "--files", str(tmp_path), "--hidden", "--glob", "*.py"],
    )

    assert result.exit_code == 0
    assert "src" in result.stdout
    assert "app.py" in result.stdout
    assert "node_modules" not in result.stdout


def test_files_mode_allows_explicit_broad_generated_root_scan(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--files",
            str(tmp_path),
            "--hidden",
            "--no-ignore",
            "--allow-broad-generated-scan",
        ],
    )

    assert result.exit_code == 0
    assert "src" in result.stdout
    assert "app.py" in result.stdout
    assert "node_modules" in result.stdout
    assert "pkg.js" in result.stdout


def test_plain_hidden_search_does_not_trigger_broad_generated_root_guard(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(hidden=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_no_ignore_content_search_allows_generated_child_dirs(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(no_ignore=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_no_ignore_content_search_allows_windows_appdata_child_dir(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "AppData").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(no_ignore=True, hidden=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_normal_no_ignore_search_allows_broad_generated_child_before_rg_passthrough(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    (tmp_path / "AppData").mkdir()
    (tmp_path / "AppData" / "hit.txt").write_text("foo\n", encoding="utf-8")
    (tmp_path / "normal.txt").write_text("foo\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "foo", str(tmp_path), "--hidden", "--no-ignore", "--cpu"],
    )

    assert result.exit_code == 0, result.output
    assert "normal.txt" in result.stdout
    assert "AppData" in result.stdout


def test_no_ignore_search_treats_cwd_generated_root_as_broad_generated_scan(
    monkeypatch, tmp_path: Path
):
    venv_root = tmp_path / ".venv"
    venv_root.mkdir()
    monkeypatch.chdir(venv_root)

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        ["."],
        SearchConfig(no_ignore=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is True
    assert generated_dirs == [".venv"]


def test_workspace_root_guard_allows_bounded_workspace_scan(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert project_dirs == []


def test_workspace_root_guard_allows_explicit_workspace_scan(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=True,
        paths_defaulted=False,
    )

    assert refused is False
    assert project_dirs == []


def test_workspace_root_guard_allows_real_repo_root(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("", encoding="utf-8")
    for project_name in ("alpha", "beta", "gamma"):
        project = repo / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(repo)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert project_dirs == []


def test_workspace_root_guard_refuses_marked_root_with_many_marked_children(tmp_path: Path):
    """Item #154: a root carrying its OWN project marker (e.g. a real-world multi-project
    workspace parent with a top-level `pyproject.toml`/`package.json`) must NOT be skipped
    outright -- it can *also* be a workspace parent when it has many independently-marked
    children. 8 marked children clears the higher marked-root threshold, so the guard must
    still refuse (the reported bug: this case previously always slipped past unbounded)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("", encoding="utf-8")
    for index in range(8):
        project = workspace / f"project-{index}"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is True
    assert project_dirs == [f"project-{index}" for index in range(8)]


def test_workspace_root_guard_allows_marked_root_with_seven_marked_children(tmp_path: Path):
    """Boundary pin for item #154's marked-root threshold (N=8): a marked root with exactly
    7 marked children must stay UNREFUSED -- one short of the higher bar a marked root needs
    before it counts as a workspace parent too (an ordinary single project can legitimately
    carry a handful of marked children, e.g. Cargo workspace members or vendored submodules,
    without itself being a workspace parent)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("", encoding="utf-8")
    for index in range(7):
        project = workspace / f"project-{index}"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert project_dirs == []


def test_scan_guard_refuses_marked_workspace_root_with_many_marked_children(tmp_path: Path):
    """Item #158 (`tg scan` sibling of #154): the broad-SCAN guard in scan_guardrails.py must
    apply the same marked-root threshold as the search guard. A marked workspace parent (its
    own top-level `pyproject.toml`) with 8 independently-marked children clears the higher
    marked-root threshold, so `find_broad_scan_refusal` must refuse it. Previously the root's
    own marker skipped it outright, so a whole-workspace `tg scan` slipped past unbounded."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("", encoding="utf-8")
    for index in range(8):
        project = workspace / f"project-{index}"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refusal = find_broad_scan_refusal([str(workspace)])

    assert refusal is not None
    assert refusal.kind == "workspace-root"
    assert refusal.names == [f"project-{index}" for index in range(8)]


def test_scan_guard_allows_marked_root_with_seven_marked_children(tmp_path: Path):
    """Boundary pin for item #158's marked-root threshold (N=8) on the scan front door: a
    marked root with exactly 7 marked children stays UNREFUSED -- one short of the higher bar
    a marked root needs before it also counts as a workspace parent (an ordinary single
    project can legitimately carry a handful of marked children without being a workspace
    parent). Mirrors the search-side boundary pin above."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("", encoding="utf-8")
    for index in range(7):
        project = workspace / f"project-{index}"
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refusal = find_broad_scan_refusal([str(workspace)])

    assert refusal is None


def test_workspace_root_guard_refuses_glob_with_implicit_path(tmp_path: Path):
    """Bug #88 (dogfood v1.54.0): `--glob` narrows WHICH files match, it does not bound how
    much of the tree must be walked to find them. When PATH was left to default (no explicit
    PATH typed -- `paths_defaulted=True`), `--glob` alone must NOT exempt a workspace-shaped
    root from this refusal, or a bare `tg search --glob X PATTERN` from a workspace root walks
    every sibling project unbounded (the reported hang)."""
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=True,
    )

    assert refused is True
    assert project_dirs == ["alpha", "beta", "gamma"]


def test_workspace_root_guard_allows_max_depth_with_implicit_path(tmp_path: Path):
    """`--max-depth` genuinely bounds the WALK itself (unlike `--glob`/`--type`, which only
    filter already-encountered files), so it remains a valid escape hatch even when PATH was
    left to default -- this is not the bug #88 shape."""
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(max_depth=1),
        allow_broad_generated_scan=False,
        paths_defaulted=True,
    )

    assert refused is False
    assert project_dirs == []


def test_vendored_root_guard_refuses_single_project_root_with_top_level_vendor_dir(
    tmp_path: Path,
):
    """Critical unscoped-search-hang fix C: `_workspace_project_child_names` SKIPS any root
    that is itself a project (has its own marker), so a single huge vendored repo with e.g.
    a committed Go `vendor/` at its own top level always slipped past
    `_should_refuse_unbounded_workspace_root_scan`. The vendored-root guard closes that gap
    with a cheap top-level-only probe (never a full walk).

    Uses `vendor/` (not `node_modules/`, review finding H1): `node_modules` is already
    walker-skipped by `DirectoryScanner`'s `_GENERATED_DIR_NAMES`, so it can never cause the
    hang this guard exists to fail fast on -- `vendor` is a genuinely walked heavy dir name
    that survives the walker-skip subtraction."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "pkg.go").write_text("package pkg\n", encoding="utf-8")

    refused, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [str(repo)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is True
    assert vendored_dirs == ["vendor"]


def test_vendored_root_guard_excludes_walker_skipped_node_modules(tmp_path: Path):
    """Review finding H1 (PR #400): `node_modules` is already hard-skipped by
    `DirectoryScanner._should_descend_dir` (via `_GENERATED_DIR_NAMES`), and `rg` respects
    `.gitignore` (where `node_modules` almost always lives) plus Fix B's per-file deadline.
    A dir the walker never descends can never cause the unscoped-search hang this guard
    exists to fail fast on, so it must NOT trigger the refusal -- doing so needlessly
    exit-2's every ordinary Node/React repo's unscoped search."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")

    refused, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [str(repo)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert vendored_dirs == []


def test_vendored_root_guard_allows_bounded_scan(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (repo / "vendor").mkdir()

    refused, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [str(repo)],
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert vendored_dirs == []


def test_vendored_root_guard_refuses_glob_with_implicit_path(tmp_path: Path):
    """Bug #88 (dogfood v1.54.0): same gap as the workspace-root guard above, but for a
    vendored-named top-level dir -- `--glob` must not exempt an implicit-path (defaulted)
    scan from the vendored-root refusal."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (repo / "vendor").mkdir()

    refused, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [str(repo)],
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=True,
    )

    assert refused is True
    assert vendored_dirs == ["vendor"]


def test_vendored_root_guard_allows_normal_small_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    refused, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [str(repo)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False
    assert vendored_dirs == []

    result = CliRunner().invoke(app, ["search", "needle", str(repo)])
    assert result.exit_code == 0, result.output


def test_plain_search_refuses_unbounded_single_repo_root_with_vendored_top_level_dir(
    tmp_path: Path,
):
    """Uses `vendor/` (not `node_modules/`, review finding H1) -- `node_modules` is
    walker-skipped so it no longer triggers this refusal; see the sibling non-regression
    test below for that case."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "pkg.go").write_text("needle\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "needle", str(repo)])

    assert result.exit_code == 2
    assert "broad root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "vendor" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_plain_search_does_not_refuse_repo_root_with_node_modules(tmp_path: Path):
    """Non-regression for review finding H1 (PR #400): `node_modules` is already
    walker-skipped by `DirectoryScanner` (and normally `.gitignore`d + bounded by Fix B's
    per-file deadline even if walked), so a plain Node/React repo's unscoped search must NOT
    be wrongly refused (exit 2) just because `node_modules/` sits at its top level."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("needle\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "needle", str(repo)])

    # Not refused (exit 2) -- a real match is exit 0, or exit 1 if the backend used for
    # this invocation doesn't surface a match through CliRunner's captured output (e.g. a
    # real `rg` subprocess passthrough writes to the OS-level fd, bypassing click's
    # in-process capture). The key regression this guards is "not wrongly refused".
    assert result.exit_code in (0, 1), result.output


def test_large_root_guard_refuses_over_ceiling_candidate_count():
    refused = _should_refuse_unbounded_large_root_scan(
        2000,
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is True


def test_large_root_guard_allows_scoped_glob_over_ceiling():
    """An explicit PATH combined with `--glob` is a deliberate, scoped request -- must still
    run, not be refused (Trap #3, unchanged by bug #88's fix)."""
    refused = _should_refuse_unbounded_large_root_scan(
        2000,
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False


def test_plain_search_refuses_over_ceiling_implicit_root_before_walk(monkeypatch, tmp_path: Path):
    """P0-1 (dogfood + external audit 2026-07-11): a bare `tg search PATTERN` (no explicit path) on a
    large ORDINARY root -- not a multi-project workspace, generated, or vendored root, so none of the
    cheap top-level guards fire -- must refuse fast via the bounded candidate-walk probe rather than
    walk the whole tree to the ~60s deadline. Critically it fires for EVERY backend, incl. the rg
    fast-path that the post-walk F6 guard skips."""
    monkeypatch.setattr("tensor_grep.cli.main._LARGE_ROOT_SCAN_FILE_CEILING", 3)
    for i in range(6):
        (tmp_path / f"mod_{i}.py").write_text("needle = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["search", "needle"])

    assert result.exit_code == 2, result.output
    assert "broad root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output


def test_plain_search_allows_under_ceiling_implicit_root(monkeypatch, tmp_path: Path):
    """The bounded probe must NOT refuse an ordinary SMALL implicit root -- a real search still runs
    (guards against the memory-warned regression where a too-broad refusal exit-2'd every search)."""
    monkeypatch.setattr("tensor_grep.cli.main._LARGE_ROOT_SCAN_FILE_CEILING", 100)
    for i in range(3):
        (tmp_path / f"mod_{i}.py").write_text("needle = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["search", "needle"])

    assert result.exit_code == 0, result.output
    assert "broad root scan refused" not in result.output


def test_large_root_guard_refuses_glob_with_implicit_path_over_ceiling():
    """Bug #88 (dogfood v1.54.0): with NO explicit PATH (`paths_defaulted=True`), `--glob`
    alone must not exempt an over-ceiling candidate count from refusal -- the ceiling is
    evaluated on the ACTUAL post-glob-filter count, so this never under-refuses a genuinely
    scoped-down glob (see the ceiling docstring)."""
    refused = _should_refuse_unbounded_large_root_scan(
        2000,
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
        paths_defaulted=True,
    )

    assert refused is True


def test_large_root_guard_allows_max_depth_with_implicit_path_over_ceiling():
    """`--max-depth` genuinely bounds the walk, so it remains a valid escape hatch even with
    an implicit (defaulted) PATH -- this is not the bug #88 shape."""
    refused = _should_refuse_unbounded_large_root_scan(
        2000,
        SearchConfig(max_depth=2),
        allow_broad_generated_scan=False,
        paths_defaulted=True,
    )

    assert refused is False


def test_large_root_guard_allows_explicit_opt_in():
    refused = _should_refuse_unbounded_large_root_scan(
        2000,
        SearchConfig(),
        allow_broad_generated_scan=True,
        paths_defaulted=True,
    )

    assert refused is False


def test_large_root_guard_allows_count_under_ceiling():
    refused = _should_refuse_unbounded_large_root_scan(
        50,
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )

    assert refused is False


def test_plain_search_refuses_unbounded_large_single_project_root(monkeypatch, tmp_path: Path):
    """F6 (dogfood v1.42.0): an unscoped `tg search` on a large SINGLE-project,
    non-vendored root matches neither `_should_refuse_unbounded_workspace_root_scan` (needs
    >=3 sibling project dirs) nor `_should_refuse_unbounded_vendored_root_scan` (needs a
    top-level vendored dir name) -- it fell through both and ran to the #400 deadline when
    no fast native/`rg` engine was available. Force native+rg off (the slow Python path is
    the engine) and assert the guard refuses instantly instead of burning the deadline."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)
    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 2000)

    result = CliRunner().invoke(app, ["search", "TODO", str(repo)])

    assert result.exit_code == 2, result.output
    assert "broad root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output
    # Deterministic replacement for a wall-clock bound. A deadline-BURNING run prints matches
    # (verified by probe: `f98.py:# TODO item 98`); a REFUSAL prints none. Both exit 2, so the
    # exit code cannot separate them -- the absence of match output is what proves the search
    # was stopped before emitting results, and it holds on any machine at any load.
    assert "TODO item" not in result.output, (
        "the guard must refuse INSTEAD of burning the deadline; match output means the search "
        "ran and emitted results before stopping: " + result.output[:400]
    )


def test_plain_search_scoped_glob_still_runs_on_large_root(monkeypatch, tmp_path: Path):
    """Trap #3: a scoped search (`--glob`) on the same large root must still RUN, not be
    refused -- otherwise this fix just recreates the #399/#405 'every big-repo query exits
    2' friction under a new guard."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)
    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 2000)

    result = CliRunner().invoke(app, ["search", "TODO", str(repo), "--glob", "*.py"])

    assert result.exit_code == 0, result.output
    assert "broad root scan refused" not in result.output


def test_plain_search_refuses_glob_with_implicit_path_on_large_root(monkeypatch, tmp_path: Path):
    """Bug #88 (dogfood v1.54.0): `tg search --glob X PATTERN` with NO positional PATH used to
    walk/search a large single-project root unbounded, because `--glob` was (wrongly) treated
    as sufficient scoping for the large-root-ceiling guard regardless of whether PATH was
    explicit. Reproduce the exact reported shape: no PATH argument at all (cwd supplies it),
    `--glob` present, over the file-count ceiling. Force native+rg off (F6 precondition,
    matching the sibling unscoped test above) and assert the guard now refuses instantly."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)
    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 2000)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["search", "TODO", "--glob", "*.py"])

    assert result.exit_code == 2, result.output
    assert "broad root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    # Deterministic replacement for a wall-clock bound. A deadline-BURNING run prints matches
    # (verified by probe: `f98.py:# TODO item 98`); a REFUSAL prints none. Both exit 2, so the
    # exit code cannot separate them -- the absence of match output is what proves the search
    # was stopped before emitting results, and it holds on any machine at any load.
    assert "TODO item" not in result.output, (
        "the guard must refuse INSTEAD of burning the deadline; match output means the search "
        "ran and emitted results before stopping: " + result.output[:400]
    )


@pytest.mark.parametrize(
    "scope_args",
    [
        ["-t", "py"],
        ["--type", "py"],
        ["-T", "py"],
        ["--iglob", "*.py"],
        ["-tpy"],  # bundled attached-value short form (rg idiom) -- re-gate BLOCK sibling
        ["-Tpy"],
        ["-itpy"],  # mid-bundle: -i then -t py
        ["-g*.py"],  # bundled attached glob -- the -g sibling of the same form-class
        ["-ig*.py"],  # mid-bundle: -i then -g *.py
    ],
)
def test_plain_search_refuses_type_and_iglob_with_implicit_path_on_large_root(
    monkeypatch, tmp_path: Path, scope_args: list[str]
):
    """Bug #88 SIBLINGS (adversarial-gate BLOCK on #480): --type/-t, --type-not/-T and --iglob
    narrow WHICH files match but do NOT bound the walk -- the guard's own scope condition is
    glob|iglob|file_type|type_not. Before the fix, a bare `tg search PAT -t py` (no PATH) on a
    large root skipped the ceiling probe (native trigger only checked globs; bootstrap routed
    bare --type/--iglob to the unguarded rg passthrough) and walked the whole tree. Same repro
    shape as the --glob test above; must refuse instantly for every walk-scope flag."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)
    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 2000)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["search", "TODO", *scope_args])

    assert result.exit_code == 2, result.output
    assert "broad root scan refused" in result.output
    # Deterministic replacement for a wall-clock bound. A deadline-BURNING run prints matches
    # (verified by probe: `f98.py:# TODO item 98`); a REFUSAL prints none. Both exit 2, so the
    # exit code cannot separate them -- the absence of match output is what proves the search
    # was stopped before emitting results, and it holds on any machine at any load.
    assert "TODO item" not in result.output, (
        "the guard must refuse INSTEAD of burning the deadline; match output means the search "
        "ran and emitted results before stopping: " + result.output[:400]
    )


def test_plain_search_refuses_glob_with_implicit_path_on_workspace_root(
    monkeypatch, tmp_path: Path
):
    """Bug #88 companion: the workspace-shaped root variant of the same gap (the actual shape
    hit in the field -- a multi-project workspace directory, not a single oversized repo).
    Deliberately does NOT force native/rg off: the workspace-root guard must fire BEFORE the
    rg-passthrough fast path is ever considered, so this proves the fix closes the gap in the
    realistic "rg is available" default configuration too."""
    for project_name in ("alpha", "beta", "gamma"):
        project = tmp_path / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["search", "TODO", "--glob", "*.py"])

    assert result.exit_code == 2, result.output
    assert "broad workspace-root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    # Deterministic replacement for a wall-clock bound. A deadline-BURNING run prints matches
    # (verified by probe: `f98.py:# TODO item 98`); a REFUSAL prints none. Both exit 2, so the
    # exit code cannot separate them -- the absence of match output is what proves the search
    # was stopped before emitting results, and it holds on any machine at any load.
    assert "TODO item" not in result.output, (
        "the guard must refuse INSTEAD of burning the deadline; match output means the search "
        "ran and emitted results before stopping: " + result.output[:400]
    )


def test_implicit_glob_walk_probe_counts_walk_not_glob_matches(tmp_path: Path):
    """Bug #88 (v1.54.1 re-harvest): the WALK-count probe must count files the walker VISITS,
    NOT post-glob matches -- a huge tree with a SELECTIVE glob (0 matches here) must still be
    flagged, because the real search must WALK the whole tree to find those matches. 1600 `.py`
    files exist; probing with a `*.nomatch` glob (0 matches) must still exceed the ceiling."""
    from tensor_grep.cli.main import (
        _LARGE_ROOT_SCAN_FILE_CEILING,
        _implicit_glob_search_walk_exceeds_ceiling,
    )

    src = tmp_path / "src"
    src.mkdir()
    for index in range(1600):
        (src / f"stub_{index}.py").write_text("x\n", encoding="utf-8")

    exceeds = _implicit_glob_search_walk_exceeds_ceiling(
        [str(tmp_path)],
        SearchConfig(glob=["*.nomatch"]),
        _LARGE_ROOT_SCAN_FILE_CEILING,
    )
    assert exceeds is True


def test_implicit_glob_walk_probe_allows_small_and_max_depth(tmp_path: Path):
    """The probe must NOT flag a small tree, and `--max-depth` (kept in the probe config) must
    genuinely bound the walk so a deep-but-shallow-scoped search is allowed."""
    from tensor_grep.cli.main import (
        _LARGE_ROOT_SCAN_FILE_CEILING,
        _implicit_glob_search_walk_exceeds_ceiling,
    )

    small = tmp_path / "small"
    small.mkdir()
    for index in range(50):
        (small / f"f_{index}.py").write_text("x\n", encoding="utf-8")
    assert not _implicit_glob_search_walk_exceeds_ceiling(
        [str(tmp_path)], SearchConfig(glob=["*.py"]), _LARGE_ROOT_SCAN_FILE_CEILING
    )

    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    for index in range(1600):
        (nested / f"f_{index}.py").write_text("x\n", encoding="utf-8")
    assert not _implicit_glob_search_walk_exceeds_ceiling(
        [str(tmp_path / "deep")],
        SearchConfig(glob=["*.py"], max_depth=1),
        _LARGE_ROOT_SCAN_FILE_CEILING,
    )


def test_plain_search_refuses_glob_implicit_path_on_marked_single_root(monkeypatch, tmp_path: Path):
    """Bug #88 (v1.54.1 re-harvest, the ACTUAL dogfood repro shape): a large single root whose TOP
    LEVEL carries a project marker (here a `package.json`, like the real `C:/dev/projects`) is
    SKIPPED by the workspace-root guard (`_path_has_project_marker` short-circuits it) and has no
    top-level vendored dir, so a bare `tg search PATTERN --glob '*'` from it used to sail straight
    into an unbounded rg passthrough (487k lines past 60s in the field). The new implicit-glob WALK
    guard must refuse it fast. Does NOT force rg off -- proves the guard fires BEFORE the
    rg-passthrough / native-delegation fast paths."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    for index in range(1600):
        (src / f"stub_{index}.py").write_text("TODO placeholder\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["search", "TODO", "--glob", "*"])

    assert result.exit_code == 2, result.output
    assert "broad root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    # Deterministic replacement for a wall-clock bound. A deadline-BURNING run prints matches
    # (verified by probe: `f98.py:# TODO item 98`); a REFUSAL prints none. Both exit 2, so the
    # exit code cannot separate them -- the absence of match output is what proves the search
    # was stopped before emitting results, and it holds on any machine at any load.
    assert "TODO item" not in result.output, (
        "the guard must refuse INSTEAD of burning the deadline; match output means the search "
        "ran and emitted results before stopping: " + result.output[:400]
    )


def test_plain_search_glob_explicit_path_still_runs_on_marked_root(monkeypatch, tmp_path: Path):
    """Trap #3 parity for the new guard: the SAME marked large root with an EXPLICIT path + glob
    must still RUN (the implicit-glob walk guard is gated on `paths_defaulted`)."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    for index in range(1600):
        (src / f"stub_{index}.py").write_text("TODO placeholder\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "TODO", str(tmp_path), "--glob", "*.py"])

    assert result.exit_code == 0, result.output
    assert "broad root scan refused" not in result.output


def test_plain_search_unscoped_still_runs_on_small_repo(monkeypatch, tmp_path: Path):
    """A small repo (below the file-count ceiling) unscoped must still run normally."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)
    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 50)

    result = CliRunner().invoke(app, ["search", "TODO", str(repo)])

    assert result.exit_code == 0, result.output
    assert "broad root scan refused" not in result.output


def test_count_matches_refuses_cleanly_when_rg_unresolvable(monkeypatch, tmp_path: Path):
    """Task #121: `--count-matches` reports ripgrep's per-OCCURRENCE count (multiple
    matches on one line each count separately), which no fallback engine can compute --
    RustCoreBackend/CPUBackend never emit more than one match per line (mirrors `-c`'s
    LINE-count contract, not `--count-matches`'s occurrence contract; see
    rust_core/src/backend_cpu.rs's own "count MATCHING LINES, not total occurrences"
    comment on its count fast path). Before this fix, routing to that fallback silently
    reported a LINE count mislabeled as an occurrence count (a 3-occurrence line
    undercounted to 1, exit 0, no visible signal) -- silent-wrong-output, not a graceful
    degrade. With rg fully unresolvable, the CLI must refuse cleanly (structured exit 2,
    actionable message) instead of a bare crash OR a silently wrong number."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)

    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "foo", str(target), "--count-matches"])

    assert result.exit_code == 2, result.output
    # Never a bare traceback/crash.
    assert "Traceback" not in result.output
    # Never a silently-wrong number standing in for the real answer (the line-count
    # fallback would have printed "2", masquerading as the occurrence count).
    assert result.output.strip() != "2"
    assert "count-matches" in result.output
    assert "rg" in result.output.lower() or "ripgrep" in result.output.lower()
    # Points the user at the working degrade path (-c/--count) that IS correct without rg.
    assert "--count" in result.output


def test_count_matches_refuses_cleanly_when_rg_unresolvable_json(monkeypatch, tmp_path: Path):
    """JSON-mode sibling of the test above: a structured, machine-readable error envelope
    (matching the established `_exit_search_error` contract other refusals already use),
    not a bare crash and not a JSON payload carrying a silently-wrong count."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)

    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "foo", str(target), "--count-matches", "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "count_matches_requires_ripgrep"
    assert "rg" in payload["detail"].lower() or "ripgrep" in payload["detail"].lower()


def test_files_with_matches_refused_when_combined_with_json(monkeypatch, tmp_path: Path):
    """P5·H2 (codex Finding 1): `-l`/`--files-with-matches` is a RAW PATH-output mode the
    tensor-grep aggregate `--json` envelope cannot express. Before the fix, `tg search --json -l`
    silently printed plain paths with exit 0 (JSON contract dropped -- verified live). It must
    refuse fail-closed with a structured exit 2, mirroring the native
    `exit_native_structured_flag_dropped` refusal (rust_core/src/main.rs)."""
    _patch_cli_dependencies(monkeypatch)
    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "foo", str(target), "--json", "-l"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "unsupported_flag"
    assert "files-with-matches" in payload["detail"]
    # No plain path masquerading as the structured answer.
    assert target.name not in result.stdout


def test_files_without_match_refused_when_combined_with_ndjson(monkeypatch, tmp_path: Path):
    """P5·H2: `--files-without-match` under `--ndjson` is the same raw-path-vs-structured
    contract drop; refuse it identically. (ndjson emits a text error via `_exit_search_error`,
    matching the native/JSON refusal style.)"""
    _patch_cli_dependencies(monkeypatch)
    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["search", "foo", str(target), "--ndjson", "--files-without-match"]
    )

    assert result.exit_code == 2, result.output
    assert "files-without-match" in result.output
    assert "refusing" in result.output


def test_files_with_matches_plain_still_works_without_json(monkeypatch, tmp_path: Path):
    """Bidirectional half of P5·H2: plain `-l` (no `--json`/`--ndjson`) is a legitimate raw
    path output and must NOT be refused. Guards the new guard's own negative arm; the broader
    `test_files_with_matches_*` suite pins the honoring behavior itself."""
    _patch_cli_dependencies(monkeypatch)
    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "foo", str(target), "-l"])

    assert result.exit_code != 2, result.output
    assert "unsupported_flag" not in result.output


def test_files_with_matches_refused_when_json_ndjson_and_format_rg_combine(monkeypatch, tmp_path):
    """P5·H2 (codex round-3 finding): the `--format rg --json` passthrough exemption must
    REQUIRE `not ndjson`. `--json --ndjson --format rg -l` has no rg-passthrough twin for the
    ndjson side (rg's `--json` events are not the tensor-grep ndjson schema), so it must
    refuse fail-closed instead of emitting a raw path with exit 0 (ndjson contract dropped).

    CliRunner does not set `sys.argv`, and `_explicit_rg_format_requested()` reads `sys.argv`
    (it deliberately discards `format_value`) -- so without stubbing argv here the search
    command would see `explicit_rg_format=False` and the test would pass even if the `not
    ndjson` term were reverted (a vacuous oracle). Stub `sys.argv` to the real invocation so
    `explicit_rg_format=True` is genuinely exercised, and add the bidirectional control."""
    _patch_cli_dependencies(monkeypatch)
    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    # The tiger: --json AND --ndjson AND --format rg. `_explicit_rg_format_requested()` reads
    # sys.argv (discarding the typer-parsed format_value), so mirror the real invocation there.
    monkeypatch.setattr(
        "sys.argv",
        ["tg", "search", str(target), "--json", "--ndjson", "--format", "rg", "-l"],
    )
    result = CliRunner().invoke(
        app, ["search", "foo", str(target), "--json", "--ndjson", "--format", "rg", "-l"]
    )
    assert result.exit_code == 2, result.output
    assert "unsupported_flag" in result.output
    assert "files-with-matches" in result.output

    # Bidirectional control: pure `--json --format rg -l` (explicit rg format, NO ndjson) is
    # the legitimately-exempt rg-passthrough case -- it must NOT be refused here.
    monkeypatch.setattr(
        "sys.argv",
        ["tg", "search", str(target), "--json", "--format", "rg", "-l"],
    )
    control = CliRunner().invoke(
        app, ["search", "foo", str(target), "--json", "--format", "rg", "-l"]
    )
    assert control.exit_code != 2, control.output
    assert "unsupported_flag" not in control.output


def test_count_matches_still_uses_ripgrep_when_available(tmp_path: Path):
    """Bidirectional half of task #121: when rg genuinely IS available, `--count-matches`
    must keep working exactly as before (real occurrence count via rg), never refuse.

    Plain (non-JSON) `--count-matches` is eligible for the raw rg passthrough fast path
    (`_can_passthrough_rg`), which streams the real `rg` subprocess's stdout directly --
    that bypasses CliRunner's captured stream (it redirects the Python-level `sys.stdout`
    object, not the OS file descriptor a real subprocess inherits), so this uses a real
    subprocess invocation instead, mirroring
    `test_debug_passthrough_keeps_stdout_match_only`'s established pattern for the same
    reason.
    """
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "search",
            "foo",
            str(target),
            "--count-matches",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    # 4 total occurrences (3 on line 1 + 1 on line 3), NOT 2 matching lines -- proves real
    # occurrence-level counting, not a line-count masquerading as one.
    assert result.stdout.strip() == "4"


def test_count_still_degrades_via_native_engine_when_rg_unresolvable(monkeypatch, tmp_path: Path):
    """Regression guard: `-c`/`--count` (LINE-count semantics) must be UNAFFECTED by the
    #121 fix -- it already degrades correctly to the native fallback engine (Pipeline's
    `count_rust_fast_path`) because the fallback's one-match-per-line model IS `-c`'s
    contract, not just an approximation of it."""
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.runtime_paths.resolve_ripgrep_binary", lambda: None)

    target = tmp_path / "sample.txt"
    target.write_text("foo bar foo baz foo\nanother line\nfoo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "foo", str(target), "--count"])

    assert result.exit_code == 0, result.output
    # 2 matching LINES (line 1 and line 3), correct for -c/--count even without rg.
    assert result.output.strip() == "2"


def test_plain_search_does_not_refuse_large_root_when_native_available(monkeypatch, tmp_path: Path):
    """Trap #2: when the fast native `tg` binary would handle this exact query, the new
    guard must NOT fire -- refusing there would convert a WORKING instant search into an
    error on every ordinary large repo."""
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_tg)

    captured: dict[str, object] = {}

    def _fake_delegate(native_binary, *, pattern, paths, config, ndjson):
        captured["native_binary"] = native_binary
        return 0

    monkeypatch.setattr(cli_main, "_delegate_to_native_tg_search", _fake_delegate)

    repo = tmp_path / "repo"
    _make_stub_file_repo(repo, 2000)

    result = CliRunner().invoke(app, ["search", "TODO", str(repo), "--json"])

    assert result.exit_code == 0, result.output
    assert captured.get("native_binary") == native_tg


def test_glob_case_insensitive_matches_case_folded_paths(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.TXT"
    target.write_text("hello\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "hello", str(tmp_path), "--glob-case-insensitive", "--glob", "*.txt"],
    )
    captured = capfd.readouterr()

    assert result.exit_code == 0
    assert "sample.TXT" in captured.out


def test_debug_passthrough_keeps_stdout_match_only(tmp_path: Path):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "--debug"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert "routing.backend=RipgrepBackend" not in result.stdout


def test_stats_passthrough_matches_ripgrep_stdout_contract(tmp_path: Path):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")
    rg_binary = resolve_ripgrep_binary()
    assert rg_binary is not None

    expected = subprocess.run(
        [str(rg_binary), "--stats", "hello", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "--stats"],
        capture_output=True,
        text=True,
        check=False,
    )

    def _normalize_stats_timing(text: str) -> str:
        normalized_lines: list[str] = []
        for line in text.splitlines():
            if line.endswith(" seconds spent searching"):
                normalized_lines.append("<SEARCH_TIME>")
                continue
            if re.fullmatch(r"\d+\.\d+ seconds(?: total)?", line):
                normalized_lines.append("<TOTAL_TIME>")
                continue
            normalized_lines.append(line)
        return "\n".join(normalized_lines)

    assert result.returncode == expected.returncode == 0
    assert _normalize_stats_timing(result.stdout) == _normalize_stats_timing(expected.stdout)
    assert result.stderr == expected.stderr


@pytest.mark.parametrize(
    ("generator", "shell"),
    [
        ("complete-bash", "bash"),
        ("complete-zsh", "zsh"),
        ("complete-fish", "fish"),
        ("complete-powershell", "powershell"),
    ],
)
def test_search_generate_should_emit_shell_completion_script(generator: str, shell: str) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--generate", generator], prog_name="tg")

    assert result.exit_code == 0
    assert result.output.strip() == get_completion_script(
        prog_name="tg",
        complete_var="_TG_COMPLETE",
        shell=shell,
    )


def test_search_generate_should_reject_unsupported_generator() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--generate", "complete-elvish"], prog_name="tg")

    assert result.exit_code == 2
    assert "Unsupported" in result.output
    assert "complete-elvish" in result.output
    assert "complete-powershell" in result.output


def test_search_generate_help_lists_only_supported_generators() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "complete-bash" in help_text
    assert "e.g. man" not in help_text


def test_search_pcre2_version_should_run_special_action_without_pattern(
    monkeypatch, tmp_path: Path
) -> None:
    rg_binary = tmp_path / "rg.exe"
    rg_binary.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: rg_binary)

    def _fake_run(cmd, capture_output=False, text=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="PCRE2 10.42\n", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--pcre2-version"])

    assert result.exit_code == 0
    assert seen["cmd"] == [str(rg_binary), "--pcre2-version"]
    assert "PCRE2 10.42" in result.stdout


def test_search_type_list_should_run_special_action_without_pattern(
    monkeypatch, tmp_path: Path
) -> None:
    rg_binary = tmp_path / "rg.exe"
    rg_binary.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: rg_binary)

    def _fake_run(cmd, capture_output=False, text=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="rust: *.rs\n", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 0
    assert seen["cmd"] == [str(rg_binary), "--type-list"]
    assert "rust: *.rs" in result.stdout


def test_search_type_list_should_use_builtin_fallback_without_native_or_rg(monkeypatch) -> None:
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: None)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 0
    assert "python: *.py" in result.stdout
    assert "rust: *.rs" in result.stdout


def test_search_type_list_should_not_mask_backend_failure(monkeypatch, tmp_path) -> None:
    native_binary = tmp_path / "tg.exe"
    native_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: None)

    def _fake_run(cmd, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="backend failed")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 2
    assert "backend failed" in result.stderr
    assert "python: *.py" not in result.stdout


def test_new_rule_should_respect_base_dir_and_requested_name(tmp_path: Path) -> None:
    runner = CliRunner()
    base_dir = tmp_path / "ast-project"

    result = runner.invoke(
        app,
        [
            "new",
            "rule",
            "demo",
            "--lang",
            "python",
            "--yes",
            "--base-dir",
            str(base_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (base_dir / "rules" / "demo.yml").exists()
    assert not (tmp_path / "sgconfig.yml").exists()
    assert not (base_dir / "rules" / "sample-rule.yml").exists()


def test_new_project_name_should_scaffold_named_directory() -> None:
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(app, ["new", "project", "demo"])

        assert result.exit_code == 0, result.output
        assert (Path("demo") / "sgconfig.yml").exists()
        assert (Path("demo") / "rules" / "sample-rule.yml").exists()
        assert (Path("demo") / "tests" / "sample-test.yml").exists()
        assert not Path("sgconfig.yml").exists()


def test_new_project_name_with_base_dir_should_scaffold_under_named_directory(
    tmp_path: Path,
) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["new", "project", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "demo" / "sgconfig.yml").exists()
    assert (tmp_path / "demo" / "rules" / "sample-rule.yml").exists()
    assert (tmp_path / "demo" / "tests" / "sample-test.yml").exists()
    assert not (tmp_path / "sgconfig.yml").exists()


def test_new_project_scaffold_refuses_symlinked_sgconfig(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "demo"
    project_dir.mkdir()

    real_target = tmp_path / "outside-missing-sgconfig.yml"
    symlink_path = project_dir / "sgconfig.yml"
    _symlink_or_skip(symlink_path, real_target)

    result = runner.invoke(app, ["new", "project", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 1, result.output
    assert "Refusing to write through a symlink" in (result.stdout + result.stderr)
    assert symlink_path.is_symlink()
    assert not real_target.exists()
    assert not (project_dir / "rules" / "sample-rule.yml").exists()
    assert not (project_dir / "tests" / "sample-test.yml").exists()


def test_new_rule_scaffold_refuses_symlinked_destination(tmp_path: Path) -> None:
    runner = CliRunner()
    target_dir = tmp_path / "rules"
    target_dir.mkdir()
    symlink_target = tmp_path / "outside-missing-rule.yml"

    symlink_path = target_dir / "sample.yml"
    _symlink_or_skip(symlink_path, symlink_target)

    result = runner.invoke(
        app, ["new", "rule", "sample", "--lang", "python", "--base-dir", str(tmp_path)]
    )

    assert result.exit_code == 1, result.output
    assert "Refusing to write through a symlink" in (result.stdout + result.stderr)
    assert symlink_path.is_symlink()
    assert not symlink_target.exists()


def test_new_scaffold_refuses_existing_file_when_no_replace(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    target = tmp_path / "rules" / "sample.yml"
    target.parent.mkdir(exist_ok=True)
    target.write_text("id: existing\n", encoding="utf-8")

    result = runner.invoke(
        app, ["new", "rule", "sample", "--lang", "python", "--base-dir", str(tmp_path)]
    )

    assert result.exit_code == 1, result.output
    assert "already exists" in (result.stdout + result.stderr).lower()


def test_new_unknown_scaffold_kind_should_reject_before_writing(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["new", "widget", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Unsupported scaffold kind" in result.stderr
    assert not (tmp_path / "sgconfig.yml").exists()
    assert not (tmp_path / "widget").exists()
    assert not (tmp_path / "rules").exists()
    assert not (tmp_path / "tests").exists()


def test_session_daemon_help_lists_lifecycle_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["session", "daemon", "--help"])

    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "status" in result.stdout
    assert "stop" in result.stdout


def test_session_context_help_mentions_daemon_flag() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["session", "context", "--help"])

    assert result.exit_code == 0
    normalized_output = re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", result.stdout))
    # The --daemon flag is documented (name + the stable head of its help). NOT asserting the
    # help's wrapped TAIL ("session daemon.") -- that word lands past a column-width-dependent
    # wrap and is truncated in narrow terminals (e.g. after adding the --max-tokens option), which
    # made this a fragile formatting-coupled assertion rather than a real contract.
    assert "-daemon" in normalized_output
    assert "warm localhost" in normalized_output


def test_lsp_help_mentions_provider_modes() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│]+", " ", help_text))
    assert "--provider" in help_text
    assert "native=repo-map only" in normalized_help
    assert "experimental" in help_text.lower()
    assert "Examples:" in help_text
    assert "--provider hybrid" in normalized_help
    assert "--debug-trace" in help_text


def test_lsp_rejects_unknown_provider_mode() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--provider", "remote"])

    assert result.exit_code != 0
    combined_output = _strip_ansi(result.stdout + result.stderr)
    assert "Unsupported LSP provider mode" in combined_output
    assert "native, lsp, hybrid" in combined_output


def test_lsp_reports_clean_error_when_ast_extra_missing(monkeypatch) -> None:
    """Item #159: `tg lsp` with a VALID provider must emit a clean, actionable error (name the
    `ast` extra + the install command) instead of a raw `ModuleNotFoundError` traceback when the
    optional `ast` extra (pygls/lsprotocol) is not installed. Simulate the missing extra by
    poisoning the lazy `lsp_server` import so it raises ImportError, exactly as a bare install
    would. Env-robust: this passes whether or not the extra is installed in the test venv."""
    monkeypatch.setitem(sys.modules, "tensor_grep.cli.lsp_server", None)
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--provider", "native"])

    assert result.exit_code != 0
    combined_output = _strip_ansi(result.stdout + result.stderr)
    assert "ast" in combined_output.lower()
    assert "pip install" in combined_output.lower()
    # The whole point: a clean message, NOT a leaked traceback / exception.
    assert "Traceback" not in combined_output
    assert "ModuleNotFoundError" not in combined_output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_lsp_rejects_unknown_provider_without_needing_ast_extra(monkeypatch) -> None:
    """Item #159: validating `--provider` must not require the optional `ast` extra -- an invalid
    provider yields the clean "Unsupported LSP provider mode" error even when `lsp_server`
    (pygls/lsprotocol) is unavailable, because provider validation now precedes the lazy import.
    This is the env-robust twin of `test_lsp_rejects_unknown_provider_mode` (which relies on the
    extra being present in the CI venv)."""
    monkeypatch.setitem(sys.modules, "tensor_grep.cli.lsp_server", None)
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--provider", "remote"])

    assert result.exit_code == 2
    combined_output = _strip_ansi(result.stdout + result.stderr)
    assert "Unsupported LSP provider mode" in combined_output
    assert "Traceback" not in combined_output


def test_lsp_debug_trace_emits_json_probe_payload(monkeypatch, tmp_path) -> None:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    def _fake_debug_trace(self, *, language, workspace_root, probe_timeout_seconds=None):
        return {
            "schema_version": 1,
            "language": language,
            "workspace_root": str(Path(workspace_root).resolve()),
            "probe_timeout_seconds": probe_timeout_seconds,
            "status": {"health_status": "ready", "lsp_proof": True},
            "trace": [{"event": "send_request", "method": "initialize"}],
            "stderr_tail": [],
        }

    monkeypatch.setattr(ExternalLSPProviderManager, "provider_debug_trace", _fake_debug_trace)

    result = CliRunner().invoke(
        app,
        [
            "lsp",
            "--debug-trace",
            "python",
            "--path",
            str(tmp_path),
            "--probe-timeout-seconds",
            "0.5",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["language"] == "python"
    assert payload["probe_timeout_seconds"] == 0.5
    assert payload["trace"][0]["method"] == "initialize"


def test_lsp_setup_help_mentions_managed_provider_install() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp-setup", "--help"], color=False)
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "--json" in help_text
    assert "managed external LSP providers" in re.sub(r"\s+", " ", help_text)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│┌┐└┘]+", " ", help_text))
    assert "does not prove semantic navigation" in normalized_help
    assert "health_status" in normalized_help


def test_doctor_help_mentions_lsp_and_json() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["doctor", "--help"])
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "--with-lsp" in help_text
    assert "--json" in help_text
    assert "--config" in help_text
    # Handle rich text wrapping that may split phrases
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│┌┐└┘]+", " ", help_text))
    assert "system, GPU, cache" in normalized_help
    assert "provider-proof diagnostics" in normalized_help
    assert "provider availability is not navigation proof" in normalized_help.lower()
    assert "health_status" in normalized_help
    assert "health_check" in normalized_help
    assert "AST" in normalized_help
    assert "PowerShell" in normalized_help
    assert "cmd.exe" in normalized_help
    assert "literal patterns" in normalized_help


def test_doctor_lsp_probe_timeout_defaults_to_windows_budget(monkeypatch) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_lsp_probe_timeout_defaults_to_provider_budget_on_posix(monkeypatch) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(cli_main.sys, "platform", "linux")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_lsp_probe_timeout_allows_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(7.5)


def test_doctor_lsp_probe_timeout_ignores_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "slow")
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_json_includes_runtime_session_and_lsp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr(
        "tensor_grep.cli.main.resolve_native_tg_binary",
        lambda: tmp_path / "rust_core" / "target" / "debug" / "tg.exe",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": True, "host": "127.0.0.1", "port": 43123, "pid": 9001},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [
            {
                "language": "python",
                "available": True,
                "running": False,
                "command": ["pyright-langserver", "--stdio"],
                "command_source": "managed",
                "managed_provider_root": str(tmp_path / "providers"),
                "last_error": None,
                "health_status": "ready",
                "health_check": "probe",
                "lsp_proof": True,
            }
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_ast_grep_status",
        lambda: {
            "schema_version": 1,
            "available": True,
            "binary": "ast-grep",
            "wrapper_backend": "AstGrepWrapperBackend",
            "required_for": "tg run ast-grep semantic options",
            "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
            "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
            "timeout_seconds": 60.0,
        },
    )
    monkeypatch.setenv("TG_RUST_EARLY_RG", "1")
    monkeypatch.setenv("TG_RUST_EARLY_POSITIONAL_RG", "1")
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setenv("TG_RESIDENT_AST", "1")
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "6.5")

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "9.9.9"
    assert payload["schema_version"] == 3
    assert payload["doctor_schema_version"] == 3
    assert payload["root"] == str(tmp_path.resolve())
    assert payload["native_tg_binary_exists"] is True
    assert payload["env"]["TG_RUST_EARLY_RG"] == "1"
    assert payload["env"]["TG_RUST_EARLY_POSITIONAL_RG"] == "1"
    assert payload["env"]["TG_FORCE_CPU"] == "1"
    assert payload["env"]["TG_RESIDENT_AST"] == "1"
    assert payload["session_daemon"]["running"] is True
    assert payload["lsp"]["enabled"] is True
    assert payload["lsp"]["schema_version"] == 2
    assert payload["lsp"]["probe_timeout_seconds"] == pytest.approx(6.5)
    assert payload["lsp"]["providers"][0]["language"] == "python"
    assert payload["lsp"]["providers"][0]["command_source"] == "managed"
    assert payload["lsp"]["providers"][0]["managed_provider_root"] == str(tmp_path / "providers")
    assert payload["lsp"]["providers"][0]["health_status"] == "ready"
    assert payload["lsp"]["providers"][0]["health_check"] == "probe"
    assert payload["lsp"]["providers"][0]["lsp_proof"] is True
    assert payload["lsp_provider_items"] == payload["lsp"]["providers"]
    assert payload["lsp"]["providers_by_language"]["python"]["health"] == "ready"
    assert payload["lsp"]["providers_by_language"]["python"]["health_status"] == "ready"
    assert payload["lsp_providers"]["python"]["health"] == "ready"
    guidance = payload["shell_escaping_guidance"]
    assert guidance["platform"] == "windows"
    assert "PowerShell double quotes expand $NAME" in guidance["powershell"]["summary"]
    assert "single quotes" in guidance["powershell"]["recommendation"]
    assert guidance["powershell"]["literal_pattern_example"] == "tg search '$NAME' ."
    assert "|" in guidance["cmd"]["metacharacters"]
    assert "^" in guidance["cmd"]["recommendation"]
    assert payload["ast_grep"]["available"] is True
    assert payload["ast_grep"]["semantic_run_options"] == [
        "--selector",
        "--strictness",
        "--stdin",
        "--globs",
    ]
    assert payload["ast_grep"]["timeout_env"] == "TG_AST_GREP_TIMEOUT_SECONDS"


def test_doctor_json_no_lsp_keeps_empty_schema_compatibility(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 3
    assert payload["doctor_schema_version"] == 3
    assert payload["lsp"]["enabled"] is False
    assert payload["lsp"]["schema_version"] == 2
    assert payload["lsp"]["providers"] == []
    assert payload["lsp"]["providers_by_language"] == {}
    assert payload["lsp_provider_items"] == []
    assert payload["lsp_providers"] == {}


def test_doctor_session_daemon_autostart_status_reflects_the_live_gate(monkeypatch) -> None:
    """The pure status-string function must reuse `_session_daemon_autostart_enabled` (the SAME
    gate the real Tier-1 fast path checks) rather than re-deriving its own copy of the rule."""
    monkeypatch.setattr(cli_main, "_session_daemon_autostart_enabled", lambda: True)
    assert cli_main._doctor_session_daemon_autostart_status() == "on-first-use (not yet warmed)"

    monkeypatch.setattr(cli_main, "_session_daemon_autostart_enabled", lambda: False)
    disabled = cli_main._doctor_session_daemon_autostart_status()
    assert "disabled" in disabled
    assert disabled != "on-first-use (not yet warmed)"


def test_doctor_session_daemon_status_adds_autostart_hint_when_stopped(
    monkeypatch, tmp_path: Path
) -> None:
    """`_doctor_session_daemon_status` (the real wrapper, unpatched) must enrich a stopped-daemon
    status with `autostart` -- patch the INNER `get_session_daemon_status` (not the wrapper
    itself) so the new enrichment logic actually executes."""
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.get_session_daemon_status",
        lambda path: {"version": 1, "root": path, "discovered": False, "running": False},
    )
    monkeypatch.setattr(cli_main, "_session_daemon_autostart_enabled", lambda: True)

    status = cli_main._doctor_session_daemon_status(str(tmp_path))

    assert status["running"] is False
    assert status["autostart"] == "on-first-use (not yet warmed)"


def test_doctor_session_daemon_status_omits_autostart_hint_when_running(
    monkeypatch, tmp_path: Path
) -> None:
    """A WARM daemon must not carry a meaningless `autostart` hint -- additive-only, conditional
    on the not-running state (mirrors the `install_hint` precedent's own conditional-add style)."""
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.get_session_daemon_status",
        lambda path: {
            "version": 1,
            "root": path,
            "discovered": False,
            "running": True,
            "host": "127.0.0.1",
            "port": 43123,
            "pid": 9001,
            "started_at": "2026-07-21T00:00:00Z",
        },
    )

    status = cli_main._doctor_session_daemon_status(str(tmp_path))

    assert status["running"] is True
    assert "autostart" not in status
