import json
import shutil
import subprocess
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


def test_checkpoint_list_explains_empty_scope(tmp_path: Path) -> None:
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
