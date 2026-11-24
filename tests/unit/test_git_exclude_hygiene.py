from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli.git_exclude_hygiene import ensure_git_info_exclude


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def test_fresh_git_repo_gets_tensor_grep_excluded_and_status_stays_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")

    tg_dir = repo / ".tensor-grep" / "sessions"
    tg_dir.mkdir(parents=True)
    (tg_dir / "index.json").write_text("{}", encoding="utf-8")

    assert ensure_git_info_exclude(repo) is True

    exclude_file = repo / ".git" / "info" / "exclude"
    assert ".tensor-grep/" in exclude_file.read_text(encoding="utf-8").splitlines()
    # Tracked .gitignore must never be mutated.
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "*.pyc\n"

    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert ".tensor-grep" not in status.stdout


def test_ensure_git_info_exclude_is_idempotent_and_preserves_crlf(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    exclude_file = repo / ".git" / "info" / "exclude"
    exclude_file.write_bytes(b"*.log\r\n")

    assert ensure_git_info_exclude(repo) is True
    first_bytes = exclude_file.read_bytes()
    assert first_bytes == b"*.log\r\n.tensor-grep/\r\n"

    assert ensure_git_info_exclude(repo) is True
    assert exclude_file.read_bytes() == first_bytes


def test_non_git_dir_fails_open(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ensure_git_info_exclude(plain) is False


def test_symlinked_git_and_exclude_are_refused(tmp_path: Path) -> None:
    # 1. Symlinked .git directory (or Windows junction)
    repo_with_link_git = tmp_path / "repo_link_git"
    repo_with_link_git.mkdir()
    real_git = tmp_path / "real_git"
    (real_git / "info").mkdir(parents=True)
    (real_git / "info" / "exclude").write_text("", encoding="utf-8")

    dot_git_link = repo_with_link_git / ".git"
    created_link = False
    try:
        os.symlink(real_git, dot_git_link, target_is_directory=True)
        created_link = True
    except OSError:
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(dot_git_link), str(real_git)],
                capture_output=True,
                text=True,
                check=False,
            )
            created_link = proc.returncode == 0
    if not created_link:
        pytest.skip("Symlink/junction creation not permitted in this environment")

    assert ensure_git_info_exclude(repo_with_link_git) is False
    assert (real_git / "info" / "exclude").read_text(encoding="utf-8") == ""

    # 2. Symlinked .git/info/exclude file
    repo_with_link_excl = tmp_path / "repo_link_excl"
    repo_with_link_excl.mkdir()
    _init_git_repo(repo_with_link_excl)
    outside = tmp_path / "outside.txt"
    outside.write_text("# outside\n", encoding="utf-8")
    excl_path = repo_with_link_excl / ".git" / "info" / "exclude"
    excl_path.unlink(missing_ok=True)
    try:
        os.symlink(outside, excl_path)
    except OSError:
        pytest.skip("File symlink creation not permitted in this environment")

    assert ensure_git_info_exclude(repo_with_link_excl) is False
    assert outside.read_text(encoding="utf-8") == "# outside\n"


def test_reparse_point_git_refused_via_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    exclude_file = repo / ".git" / "info" / "exclude"
    before = exclude_file.read_bytes()

    real_lstat = os.lstat

    def fake_lstat(path: Path | str, *args: object, **kwargs: object) -> os.stat_result:
        res = real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path) == repo / ".git":
            # Simulate a symlink mode bit on .git
            fake_list = list(res)
            fake_list[0] = stat.S_IFLNK | 0o777
            return os.stat_result(fake_list)
        return res

    monkeypatch.setattr("tensor_grep.cli.git_exclude_hygiene.os.lstat", fake_lstat)
    assert ensure_git_info_exclude(repo) is False
    assert exclude_file.read_bytes() == before


def test_gitdir_outside_worktree_layout_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside_target"
    outside.mkdir()
    malicious_repo = tmp_path / "malicious_repo"
    malicious_repo.mkdir()
    (malicious_repo / ".git").write_text(f"gitdir: {outside}\n", encoding="utf-8")

    assert ensure_git_info_exclude(malicious_repo) is False
    assert not (outside / "info").exists()


def test_valid_worktree_gitdir_updates_common_exclude(tmp_path: Path) -> None:
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    _init_git_repo(main_repo)
    wt_gitdir = main_repo / ".git" / "worktrees" / "wt1"
    wt_gitdir.mkdir(parents=True)

    wt_repo = tmp_path / "wt_repo"
    wt_repo.mkdir()
    (wt_repo / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    (wt_gitdir / "gitdir").write_text(f"{wt_repo / '.git'}\n", encoding="utf-8")

    assert ensure_git_info_exclude(wt_repo) is True
    exclude_text = (main_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".tensor-grep/" in exclude_text.splitlines()


def test_worktree_mismatched_backpointer_and_info_reparse_and_replace_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Mismatched worktree backpointer is refused
    main_repo = tmp_path / "main_bp"
    main_repo.mkdir()
    _init_git_repo(main_repo)
    wt_gitdir = main_repo / ".git" / "worktrees" / "wt_bad"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "gitdir").write_text(str(tmp_path / "other_repo" / ".git"), encoding="utf-8")

    crafted = tmp_path / "crafted"
    crafted.mkdir()
    (crafted / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    assert ensure_git_info_exclude(crafted) is False
    assert ".tensor-grep/" not in (main_repo / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    )

    # 2. Reparse-point `info` directory is refused
    repo_info = tmp_path / "repo_info"
    repo_info.mkdir()
    _init_git_repo(repo_info)
    real_lstat = os.lstat

    def fake_info_lstat(path: Path | str, *args: object, **kwargs: object) -> os.stat_result:
        res = real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path) == repo_info / ".git" / "info":
            fake_list = list(res)
            fake_list[0] = stat.S_IFLNK | 0o777
            return os.stat_result(fake_list)
        return res

    with monkeypatch.context() as m:
        m.setattr("tensor_grep.cli.git_exclude_hygiene.os.lstat", fake_info_lstat)
        assert ensure_git_info_exclude(repo_info) is False

    # 3. os.replace failure cleans up temp file and fails open (returns False)
    repo_err = tmp_path / "repo_err"
    repo_err.mkdir()
    _init_git_repo(repo_err)
    info_dir = repo_err / ".git" / "info"
    with monkeypatch.context() as m:
        m.setattr(
            "tensor_grep.cli.git_exclude_hygiene.os.replace",
            lambda _a, _b: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert ensure_git_info_exclude(repo_err) is False
    assert not list(info_dir.glob(".exclude-*.tmp"))
