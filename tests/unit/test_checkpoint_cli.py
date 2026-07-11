import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_checkpoint_create_list_and_undo_restores_non_git_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()

    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "filesystem-snapshot"
    assert payload["undo_argv"] == [
        "tg",
        "checkpoint",
        "undo",
        payload["checkpoint_id"],
        str(project.resolve()),
    ]
    checkpoint_id = payload["checkpoint_id"]

    list_result = runner.invoke(app, ["checkpoint", "list", str(project), "--json"])
    assert list_result.exit_code == 0
    listed = json.loads(list_result.stdout)
    assert listed["root"] == str(project.resolve())
    assert listed["checkpoint_count"] == 1
    assert listed["checkpoints"][0]["checkpoint_id"] == checkpoint_id

    source_file.write_text("print('after')\n", encoding="utf-8")
    extra_file = project / "generated.py"
    extra_file.write_text("print('new')\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(project), "--json"],
    )
    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["mode"] == "filesystem-snapshot"
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert not extra_file.exists()


def test_checkpoint_create_for_file_scope_only_restores_that_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")
    sibling_file = project / "notes.txt"
    sibling_file.write_text("leave me alone\n", encoding="utf-8")

    runner = CliRunner()

    create_result = runner.invoke(app, ["checkpoint", "create", str(source_file), "--json"])
    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "filesystem-snapshot"
    assert payload["root"] == str(project.resolve())
    assert payload["file_count"] == 1
    assert payload["undo_argv"] == [
        "tg",
        "checkpoint",
        "undo",
        payload["checkpoint_id"],
        str(source_file.resolve()),
    ]
    checkpoint_id = payload["checkpoint_id"]

    source_file.write_text("print('after')\n", encoding="utf-8")
    sibling_file.write_text("still present\n", encoding="utf-8")
    generated_file = project / "generated.py"
    generated_file.write_text("print('new')\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(source_file), "--json"],
    )

    assert undo_result.exit_code == 0
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert sibling_file.read_text(encoding="utf-8") == "still present\n"
    assert generated_file.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_checkpoint_create_for_file_inside_git_repo_stays_file_scoped(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_dir = project / "src"
    source_dir.mkdir()
    source_file = source_dir / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")
    sibling_file = source_dir / "notes.txt"
    sibling_file.write_text("leave me alone\n", encoding="utf-8")
    ignored_dir = project / "artifacts" / "external_repos" / "chalk"
    ignored_dir.mkdir(parents=True)
    ignored_file = ignored_dir / "README.md"
    ignored_file.write_text("ignored\n", encoding="utf-8")
    (project / ".gitignore").write_text("artifacts/\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(source_file), "--json"])

    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "filesystem-snapshot"
    assert payload["root"] == str(source_dir.resolve())
    assert payload["file_count"] == 1
    checkpoint_id = payload["checkpoint_id"]

    source_file.write_text("print('after')\n", encoding="utf-8")
    sibling_file.write_text("still present\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(source_file), "--json"],
    )

    assert undo_result.exit_code == 0
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert sibling_file.read_text(encoding="utf-8") == "still present\n"
    assert ignored_file.read_text(encoding="utf-8") == "ignored\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_checkpoint_undo_git_scope_uses_git_entries_instead_of_filesystem_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    from tensor_grep.cli import checkpoint_store

    checkpoint = checkpoint_store.create_checkpoint(str(project))
    source_file.write_text("print('after')\n", encoding="utf-8")

    def fail_filesystem_walk(root: Path) -> dict[str, bool]:
        raise AssertionError(f"filesystem walk should not run for git checkpoint undo: {root}")

    monkeypatch.setattr(checkpoint_store, "_filesystem_snapshot_entries", fail_filesystem_walk)

    restored = checkpoint_store.undo_checkpoint(checkpoint.checkpoint_id, str(project))

    assert restored.mode == "git-worktree-snapshot"
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"


def test_checkpoint_list_explains_empty_scope(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import checkpoint_store

    monkeypatch.setattr(checkpoint_store, "discover_nearby_checkpoint_scopes", lambda _path=".": [])

    runner = CliRunner()
    result = runner.invoke(app, ["checkpoint", "list", str(tmp_path)])

    assert result.exit_code == 0
    assert f"Checkpoint root: {tmp_path.resolve()}" in result.stdout
    assert "No checkpoints found under this scope." in result.stdout
    assert "Use `tg checkpoint list PATH --discover`" in result.stdout


def test_checkpoint_list_discover_finds_child_checkpoint_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    discover_result = runner.invoke(
        app,
        ["checkpoint", "list", str(workspace), "--discover", "--json"],
    )

    assert discover_result.exit_code == 0
    payload = json.loads(discover_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_discover_finds_artifacts_checkpoint_scope(tmp_path: Path) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "artifacts" / "rewrite_checkpoint_case_codex"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]
    checkpoint_store._discovery_cache_path(workspace.resolve()).unlink(missing_ok=True)

    discover_result = runner.invoke(
        app,
        ["checkpoint", "list", str(workspace), "--discover", "--json"],
    )

    assert discover_result.exit_code == 0
    payload = json.loads(discover_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_snapshot_still_excludes_artifacts_content(tmp_path: Path) -> None:
    project = tmp_path / "project"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    (project / "sample.py").write_text("print('tracked')\n", encoding="utf-8")
    (artifacts / "generated.py").write_text("print('generated')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["checkpoint", "create", str(project), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "filesystem-snapshot"
    assert payload["file_count"] == 1


def test_checkpoint_discover_ignores_stale_false_negative_cache_for_artifacts_policy(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "artifacts" / "rewrite_checkpoint_case_codex"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    checkpoint = checkpoint_store.create_checkpoint(str(project))
    cache_path = checkpoint_store._discovery_cache_path(workspace.resolve())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "version": max(0, checkpoint_store._DISCOVERY_CACHE_VERSION - 1),
            "entries": {
                checkpoint_store._discovery_cache_key(
                    full=False,
                    max_depth=checkpoint_store._DISCOVERY_MAX_DEPTH,
                ): {
                    "created_at_epoch_s": time.time(),
                    "index_paths": [],
                }
            },
        }),
        encoding="utf-8",
    )

    scopes = checkpoint_store.discover_checkpoint_scopes(str(workspace))

    assert scopes
    assert scopes[0].root == str(project.resolve())
    assert scopes[0].checkpoints[0].checkpoint_id == checkpoint.checkpoint_id


def test_checkpoint_discovery_cache_preserves_truncated_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    (workspace / "one").mkdir(parents=True)
    (workspace / "two").mkdir()
    monkeypatch.setattr(checkpoint_store, "_DISCOVERY_MAX_DIRECTORIES", 1)

    first = checkpoint_store.discover_checkpoint_scopes_result(str(workspace))
    assert first.truncated is True

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("cached truncated discovery should avoid tree walk")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    second = checkpoint_store.discover_checkpoint_scopes_result(str(workspace))

    assert second.truncated is True


def test_checkpoint_list_discover_help_mentions_artifacts_exception() -> None:
    result = CliRunner().invoke(app, ["checkpoint", "list", "--help"])

    assert result.exit_code == 0
    assert "artifacts" in result.stdout
    assert "checkpoint scopes" in result.stdout


def test_checkpoint_list_auto_discovers_child_scope_when_direct_scope_is_empty(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["auto_discovered"] is True
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_auto_discovery_checks_nearby_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    nested_project = workspace / "group" / "deep_project"
    project.mkdir(parents=True)
    nested_project.mkdir(parents=True)
    (project / "sample.py").write_text("print('nearby')\n", encoding="utf-8")
    (nested_project / "sample.py").write_text("print('nested')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]
    nested_result = runner.invoke(app, ["checkpoint", "create", str(nested_project), "--json"])
    assert nested_result.exit_code == 0

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["auto_discovered"] is True
    assert [scope["root"] for scope in payload["discovered_scopes"]] == [str(project.resolve())]
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_discover_keeps_bounded_recursive_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested_project = workspace / "group" / "deep_project"
    nested_project.mkdir(parents=True)
    (nested_project / "sample.py").write_text("print('nested')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(nested_project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--discover", "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["discovered_scopes"][0]["root"] == str(nested_project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_discover_reuses_valid_index_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    checkpoint = checkpoint_store.create_checkpoint(str(project))

    first = checkpoint_store.discover_checkpoint_scopes(str(workspace))
    assert first[0].checkpoints[0].checkpoint_id == checkpoint.checkpoint_id

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("valid checkpoint discovery cache should avoid tree walk")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    second = checkpoint_store.discover_checkpoint_scopes(str(workspace))

    assert second[0].root == str(project.resolve())
    assert second[0].checkpoints[0].checkpoint_id == checkpoint.checkpoint_id


def test_checkpoint_create_primes_parent_discovery_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "group" / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    checkpoint = checkpoint_store.create_checkpoint(str(project))
    cache_path = checkpoint_store._discovery_cache_path(workspace.resolve())
    assert cache_path.exists()

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("primed checkpoint discovery should avoid tree walk")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    scopes = checkpoint_store.discover_checkpoint_scopes(str(workspace))

    assert [scope.root for scope in scopes] == [str(project.resolve())]
    assert scopes[0].checkpoints[0].checkpoint_id == checkpoint.checkpoint_id


def test_checkpoint_discovery_cache_roots_never_include_filesystem_anchor(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli import checkpoint_store

    anchor = Path(tmp_path.anchor)
    shallow_root = anchor / "tg-cache-root-a" / "tg-cache-root-b" / "tg-cache-root-c"

    cache_roots = checkpoint_store._bounded_discovery_cache_roots_for_checkpoint(shallow_root)

    assert anchor not in cache_roots
    assert all(candidate.parent != candidate for candidate in cache_roots)


def test_checkpoint_discovery_cache_roots_stop_at_user_home(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import checkpoint_store

    home = tmp_path / "Users" / "oimir"
    project = home / "fixture" / "project"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        checkpoint_store,
        "_checkpoint_discovery_home_boundary",
        lambda: home.resolve(),
    )

    cache_roots = checkpoint_store._bounded_discovery_cache_roots_for_checkpoint(project)

    assert home.resolve() in cache_roots
    assert home.resolve().parent not in cache_roots


def test_checkpoint_create_does_not_fail_when_parent_cache_is_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import checkpoint_store

    project = tmp_path / "Users" / "oimir" / "fixture" / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    blocked_root = project.resolve().parent
    blocked_file = tmp_path / "blocked-cache-parent"
    blocked_file.write_text("not a directory\n", encoding="utf-8")
    original_discovery_cache_path = checkpoint_store._discovery_cache_path

    def guarded_discovery_cache_path(search_root: Path) -> Path:
        if search_root == blocked_root:
            return blocked_file / "child" / "checkpoint-discovery-cache.json"
        return original_discovery_cache_path(search_root)

    monkeypatch.setattr(
        checkpoint_store,
        "_discovery_cache_path",
        guarded_discovery_cache_path,
    )

    checkpoint = checkpoint_store.create_checkpoint(str(project))

    assert checkpoint.checkpoint_id.startswith("ckpt-")


def test_checkpoint_create_merges_parent_discovery_cache(tmp_path: Path) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    checkpoint_store.create_checkpoint(str(project))

    assert checkpoint_store.discover_checkpoint_scopes(str(workspace))
    cache_path = checkpoint_store._discovery_cache_path(workspace.resolve())
    assert cache_path.exists()

    second_project = workspace / "second"
    second_project.mkdir()
    (second_project / "sample.py").write_text("print('second')\n", encoding="utf-8")
    second_checkpoint = checkpoint_store.create_checkpoint(str(second_project))

    scopes = checkpoint_store.discover_checkpoint_scopes(str(workspace))
    assert cache_path.exists()
    assert [scope.root for scope in scopes] == [
        str(project.resolve()),
        str(second_project.resolve()),
    ]
    assert scopes[1].checkpoints[0].checkpoint_id == second_checkpoint.checkpoint_id


def test_checkpoint_list_discover_uses_primed_cache_for_multiple_scopes_without_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    checkpoint_ids: list[str] = []
    runner = CliRunner()
    for index in range(4):
        project = workspace / f"project-{index}"
        project.mkdir(parents=True)
        (project / "sample.py").write_text(f"print({index})\n", encoding="utf-8")
        create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
        assert create_result.exit_code == 0
        checkpoint_ids.append(json.loads(create_result.stdout)["checkpoint_id"])

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("primed checkpoint discovery should avoid tree walk")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--discover", "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 4
    assert [scope["checkpoints"][0]["checkpoint_id"] for scope in payload["discovered_scopes"]] == (
        checkpoint_ids
    )


def test_checkpoint_discover_rebuilds_missing_index_from_metadata(tmp_path: Path) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    checkpoint = checkpoint_store.create_checkpoint(str(project))
    checkpoint_store._index_path(project.resolve()).unlink()

    scopes = checkpoint_store.discover_checkpoint_scopes(str(workspace))

    assert [scope.root for scope in scopes] == [str(project.resolve())]
    assert scopes[0].checkpoints[0].checkpoint_id == checkpoint.checkpoint_id
    assert checkpoint_store._index_path(project.resolve()).exists()


def test_checkpoint_list_discover_reports_truncated_bounded_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")
    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_store._discovery_cache_path(workspace.resolve()).unlink()
    monkeypatch.setattr(checkpoint_store, "_DISCOVERY_MAX_DIRECTORIES", 1)

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--discover", "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["truncated"] is True
    assert "use --discover-full" in payload["warning"]


def test_checkpoint_list_auto_discovery_does_not_use_unbounded_rglob(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    def fail_rglob(self: Path, pattern: str):
        raise AssertionError(f"unbounded rglob should not run for {self} pattern={pattern}")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["auto_discovered"] is True
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_auto_discovery_does_not_use_bounded_recursive_walk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("default checkpoint list should not use bounded recursive discovery")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["auto_discovered"] is True
    assert payload["discovered_scopes"][0]["root"] == str(project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_default_discovery_skips_generated_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    generated_project = workspace / "node_modules" / "dep"
    generated_project.mkdir(parents=True)
    (generated_project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(generated_project), "--json"])
    assert create_result.exit_code == 0

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 0
    assert "auto_discovered" not in payload
    assert "discovered_scopes" not in payload


def test_checkpoint_list_discover_full_can_include_generated_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    generated_project = workspace / "node_modules" / "dep"
    generated_project.mkdir(parents=True)
    (generated_project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(generated_project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    list_result = runner.invoke(
        app,
        ["checkpoint", "list", str(workspace), "--discover-full", "--json"],
    )

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["checkpoint_count"] == 1
    assert payload["discovered_scopes"][0]["root"] == str(generated_project.resolve())
    assert payload["discovered_scopes"][0]["checkpoints"][0]["checkpoint_id"] == checkpoint_id


def test_checkpoint_list_keeps_direct_scope_when_records_exist(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    (workspace / "workspace.py").write_text("print('workspace')\n", encoding="utf-8")
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    workspace_create = runner.invoke(app, ["checkpoint", "create", str(workspace), "--json"])
    assert workspace_create.exit_code == 0
    workspace_checkpoint_id = json.loads(workspace_create.stdout)["checkpoint_id"]
    child_create = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert child_create.exit_code == 0

    list_result = runner.invoke(app, ["checkpoint", "list", str(workspace), "--json"])

    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout)
    assert payload["root"] == str(workspace.resolve())
    assert payload["checkpoint_count"] == 1
    assert payload["checkpoints"][0]["checkpoint_id"] == workspace_checkpoint_id
    assert "discovered_scopes" not in payload


def test_checkpoint_undo_last_restores_latest_child_scope_checkpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    project.mkdir(parents=True)
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    first_create = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert first_create.exit_code == 0
    source_file.write_text("print('middle')\n", encoding="utf-8")
    second_create = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert second_create.exit_code == 0
    latest_checkpoint_id = json.loads(second_create.stdout)["checkpoint_id"]
    source_file.write_text("print('after')\n", encoding="utf-8")

    undo_result = runner.invoke(app, ["checkpoint", "undo", "--last", str(workspace), "--json"])

    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["checkpoint_id"] == latest_checkpoint_id
    assert restored["root"] == str(project.resolve())
    assert source_file.read_text(encoding="utf-8") == "print('middle')\n"


def test_checkpoint_undo_last_uses_cached_nested_scope_without_tree_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import checkpoint_store

    workspace = tmp_path / "workspace"
    project = workspace / "group" / "project"
    project.mkdir(parents=True)
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]
    source_file.write_text("print('after')\n", encoding="utf-8")

    def fail_bounded_walk(*_args, **_kwargs):
        raise AssertionError("undo --last should use cached discovery instead of tree walk")

    monkeypatch.setattr(checkpoint_store, "_bounded_checkpoint_index_paths", fail_bounded_walk)

    undo_result = runner.invoke(app, ["checkpoint", "undo", "--last", str(workspace), "--json"])

    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["root"] == str(project.resolve())
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"


def test_checkpoint_undo_last_rejects_broad_path_with_multiple_child_scopes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    project_a = workspace / "project-a"
    project_b = workspace / "project-b"
    project_a.mkdir(parents=True)
    project_b.mkdir()
    (project_a / "sample.py").write_text("print('a')\n", encoding="utf-8")
    (project_b / "sample.py").write_text("print('b')\n", encoding="utf-8")

    runner = CliRunner()
    assert runner.invoke(app, ["checkpoint", "create", str(project_a), "--json"]).exit_code == 0
    assert runner.invoke(app, ["checkpoint", "create", str(project_b), "--json"]).exit_code == 0

    undo_result = runner.invoke(app, ["checkpoint", "undo", "--last", str(workspace)])

    assert undo_result.exit_code == 1
    assert "Multiple checkpoint scopes found" in undo_result.stderr
    assert "pass a narrower PATH or explicit checkpoint id" in undo_result.stderr


def test_checkpoint_undo_last_rejects_explicit_checkpoint_id(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("print('before')\n", encoding="utf-8")

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    checkpoint_id = json.loads(create_result.stdout)["checkpoint_id"]

    undo_result = runner.invoke(app, ["checkpoint", "undo", checkpoint_id, str(project), "--last"])

    assert undo_result.exit_code == 1
    assert "Use either a checkpoint id or --last, not both." in undo_result.stderr


def test_checkpoint_undo_last_fails_clearly_when_no_checkpoints(tmp_path: Path) -> None:
    runner = CliRunner()

    undo_result = runner.invoke(app, ["checkpoint", "undo", "--last", str(tmp_path)])

    assert undo_result.exit_code == 1
    assert "No checkpoints found" in undo_result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_checkpoint_create_and_undo_reports_git_mode(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    runner = CliRunner()
    create_result = runner.invoke(app, ["checkpoint", "create", str(project), "--json"])
    assert create_result.exit_code == 0
    payload = json.loads(create_result.stdout)
    assert payload["mode"] == "git-worktree-snapshot"
    checkpoint_id = payload["checkpoint_id"]

    source_file.write_text("print('changed')\n", encoding="utf-8")
    untracked = project / "notes.txt"
    untracked.write_text("scratch\n", encoding="utf-8")

    undo_result = runner.invoke(
        app,
        ["checkpoint", "undo", checkpoint_id, str(project), "--json"],
    )
    assert undo_result.exit_code == 0
    restored = json.loads(undo_result.stdout)
    assert restored["mode"] == "git-worktree-snapshot"
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert not untracked.exists()


# --- audit #130d: checkpoint create on a repo containing a git submodule / embedded repo ---
#
# `git ls-files` reports a submodule (or any nested repo tracked as a gitlink, mode 160000)
# as ONE path -- it never expands into the submodule's own tracked files -- and that path is
# a real directory on disk (e.g. `benchmarks/external_repos/chalk` in this very repo). Before
# the fix, `_git_snapshot_entries` had no `is_dir()` guard, so the gitlink path was recorded
# as an existing entry and `create_checkpoint`'s copy loop tried `shutil.copy2()` on a
# directory, crashing (`IsADirectoryError` on POSIX, `PermissionError` on Windows -- the OS
# error class differs by platform, but both are avoided entirely by skipping the entry).


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _add_nested_repo_as_gitlink(project: Path, rel_path: str) -> Path:
    """Create a standalone nested git repo at ``project/rel_path`` and register it in
    ``project``'s index as a gitlink (mode 160000) -- the same on-disk shape as a real
    ``git submodule add`` (a tracked directory containing its own ``.git``), without needing
    submodule/protocol machinery in the test fixture.

    Reproduces the ``benchmarks/external_repos/chalk``-style embedded-repo layout that
    crashes ``checkpoint create`` (audit #130d): the gitlink path is a real directory on
    disk that ``git ls-files`` reports as a single tracked path, never expanded.
    """
    nested = project / rel_path
    nested.mkdir(parents=True)
    _init_git_repo(nested)
    (nested / "file.txt").write_text("nested\n", encoding="utf-8")
    _git_commit_all(nested, "nested-init")
    nested_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=nested,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{nested_sha},{rel_path}"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    return nested


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_skips_git_submodule_without_crashing(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "tracked.py").write_text("print('hi')\n", encoding="utf-8")
    _init_git_repo(project)
    _git_commit_all(project, "init")
    _add_nested_repo_as_gitlink(project, "vendor/nested_repo")
    _git_commit_all(project, "add gitlink")

    from tensor_grep.cli import checkpoint_store

    result = checkpoint_store.create_checkpoint(str(project))

    assert result.mode == "git-worktree-snapshot"
    assert result.file_count == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_discloses_skipped_nested_repo(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "tracked.py").write_text("print('hi')\n", encoding="utf-8")
    _init_git_repo(project)
    _git_commit_all(project, "init")
    _add_nested_repo_as_gitlink(project, "vendor/nested_repo")
    _git_commit_all(project, "add gitlink")

    from tensor_grep.cli import checkpoint_store

    result = checkpoint_store.create_checkpoint(str(project))

    assert result.skipped_nested_repos == ["vendor/nested_repo"]

    metadata = checkpoint_store.load_checkpoint_metadata(result.checkpoint_id, str(project))
    assert metadata["skipped_nested_repos"] == ["vendor/nested_repo"]
    # The excluded entry must never reach `entries` -- that's what `undo_checkpoint`'s
    # pre-flight iterates, and a phantom directory entry there would choke it on undo.
    assert "vendor/nested_repo" not in metadata["entries"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_scoped_to_subdir_still_captures_all_files(tmp_path: Path) -> None:
    """Regression: checkpointing a subdirectory (not git top-level) takes the
    filesystem-snapshot path (_filesystem_snapshot_entries), which the new is_dir() gitlink
    skip in _git_snapshot_entries never touches -- it must keep capturing every file exactly
    as it did before this fix."""
    project = tmp_path / "repo"
    project.mkdir()
    source_dir = project / "src"
    source_dir.mkdir()
    (source_dir / "a.py").write_text("print('a')\n", encoding="utf-8")
    (source_dir / "b.py").write_text("print('b')\n", encoding="utf-8")
    nested_dir = source_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "c.py").write_text("print('c')\n", encoding="utf-8")
    _init_git_repo(project)
    _git_commit_all(project, "init")

    from tensor_grep.cli import checkpoint_store

    result = checkpoint_store.create_checkpoint(str(source_dir))

    assert result.mode == "filesystem-snapshot"
    assert result.file_count == 3
    metadata = checkpoint_store.load_checkpoint_metadata(result.checkpoint_id, str(source_dir))
    assert sorted(metadata["entries"].keys()) == ["a.py", "b.py", "nested/c.py"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_then_undo_is_clean_with_skipped_submodule(tmp_path: Path) -> None:
    """e2e: the excluded gitlink entry is absent from metadata, so undo_checkpoint's
    pre-flight (which iterates every metadata entry) never chokes on a phantom directory --
    and undo's restore/removal sweep must leave the nested repo itself untouched."""
    project = tmp_path / "repo"
    project.mkdir()
    tracked_file = project / "tracked.py"
    tracked_file.write_text("print('before')\n", encoding="utf-8")
    _init_git_repo(project)
    _git_commit_all(project, "init")
    _add_nested_repo_as_gitlink(project, "vendor/nested_repo")
    _git_commit_all(project, "add gitlink")

    from tensor_grep.cli import checkpoint_store

    result = checkpoint_store.create_checkpoint(str(project))
    tracked_file.write_text("print('after')\n", encoding="utf-8")

    restored = checkpoint_store.undo_checkpoint(result.checkpoint_id, str(project))

    assert restored.mode == "git-worktree-snapshot"
    assert tracked_file.read_text(encoding="utf-8") == "print('before')\n"
    assert (project / "vendor" / "nested_repo" / "file.txt").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_does_not_skip_tracked_symlink_to_directory(tmp_path: Path) -> None:
    """The is_dir() gitlink-skip guard must not swallow a legitimately tracked SYMLINK whose
    target happens to be a directory -- git stores that as a mode-120000 blob (a normal file
    entry), not a gitlink, and it was already handled correctly by the copy loop's
    `shutil.copy2(..., follow_symlinks=False)` (copies the link itself). Only a genuine
    nested-repo directory (no .git-boundary symlink involved) should ever be skipped."""
    project = tmp_path / "repo"
    project.mkdir()
    (project / "tracked.py").write_text("print('hi')\n", encoding="utf-8")
    outside_target = tmp_path / "outside_target"
    outside_target.mkdir()
    (outside_target / "inner.txt").write_text("target content\n", encoding="utf-8")
    try:
        (project / "link_to_dir").symlink_to(outside_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")
    _init_git_repo(project)
    _git_commit_all(project, "init")

    from tensor_grep.cli import checkpoint_store

    result = checkpoint_store.create_checkpoint(str(project))

    assert result.skipped_nested_repos == []
    assert result.file_count == 2

    snapshot = checkpoint_store._snapshot_path(project, result.checkpoint_id)
    snapshotted_link = snapshot / "link_to_dir"
    assert snapshotted_link.is_symlink()
    # audit HIGH (symlink disclosure, pre-existing contract): the snapshot must never contain
    # the out-of-root target's content under a regular file.
    for path in snapshot.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert "target content" not in path.read_text(encoding="utf-8", errors="ignore")
