from collections.abc import Iterator
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map
from tensor_grep.cli import repo_map_gitignore as owner


@pytest.fixture
def clear_gitignore_loader_cache() -> Iterator[None]:
    repo_map._load_gitignore_matcher.cache_clear()
    try:
        yield
    finally:
        repo_map._load_gitignore_matcher.cache_clear()


def test_GitignoreAliases_CurrentOwner_PreservesObjectIdentity() -> None:
    assert repo_map._gitignore_pattern_to_regex is owner._gitignore_pattern_to_regex
    assert repo_map._GitignoreMatcher is owner._GitignoreMatcher
    assert repo_map._load_gitignore_matcher is owner._load_gitignore_matcher


@pytest.mark.usefixtures("clear_gitignore_loader_cache")
def test_GitignoreLoader_CachedThenCleared_RereadsRules(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("old.tmp\n", encoding="utf-8")

    initial_matcher = owner._load_gitignore_matcher(str(tmp_path))
    cached_matcher = repo_map._load_gitignore_matcher(str(tmp_path))
    gitignore.write_text("new.tmp\n", encoding="utf-8")

    assert cached_matcher is initial_matcher
    assert initial_matcher.is_ignored(tmp_path / "old.tmp", is_dir=False)
    assert initial_matcher.check(tmp_path / "new.tmp", is_dir=False) is None

    repo_map._load_gitignore_matcher.cache_clear()
    refreshed_matcher = owner._load_gitignore_matcher(str(tmp_path))

    assert refreshed_matcher is not initial_matcher
    assert refreshed_matcher.is_ignored(tmp_path / "new.tmp", is_dir=False)
    assert refreshed_matcher.check(tmp_path / "old.tmp", is_dir=False) is None
    assert repo_map._load_gitignore_matcher.cache_parameters() == {
        "maxsize": 64,
        "typed": False,
    }


@pytest.mark.usefixtures("clear_gitignore_loader_cache")
def test_RepoMapBuild_ChangedGitignore_InvalidatesLoaderCache(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("old.tmp\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    initial_matcher = repo_map._load_gitignore_matcher(str(tmp_path))
    gitignore.write_text("new.tmp\n", encoding="utf-8")
    repo_map.build_repo_map(tmp_path, max_repo_files=1)
    refreshed_matcher = repo_map._load_gitignore_matcher(str(tmp_path))

    assert refreshed_matcher is not initial_matcher
    assert refreshed_matcher.is_ignored(tmp_path / "new.tmp", is_dir=False)
    assert refreshed_matcher.check(tmp_path / "old.tmp", is_dir=False) is None


def _unexpected_path_resolve(*args: object, **kwargs: object) -> Path:
    raise AssertionError("matcher entry checks must not resolve paths")


def test_GitignoreMatcher_EntryCheck_DoesNotResolvePaths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    matcher = repo_map._GitignoreMatcher(root, ["link.py"])
    outside = root.parent / f"{root.name}-outside"

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", _unexpected_path_resolve)
        absolute_match = matcher.check(root / "link.py", is_dir=False)
        relative_match = matcher.check(Path("link.py"), is_dir=False)
        unrelated_match = matcher.check(root / "other.txt", is_dir=False)
        root_match = matcher.check(root, is_dir=True)
        outside_match = matcher.check(outside, is_dir=False)

    assert absolute_match is True
    assert relative_match is True
    assert unrelated_match is None
    assert root_match is None
    assert outside_match is None


def test_GitignoreStack_ChildNegation_OverridesParentOnlyForMatchingPath(
    tmp_path: Path,
) -> None:
    parent = repo_map._GitignoreMatcher(tmp_path, ["*.log"])
    child = repo_map._GitignoreMatcher(tmp_path / "sub", ["!keep.log"])
    keep_log = tmp_path / "sub" / "keep.log"
    other_log = tmp_path / "sub" / "other.log"

    assert child.check(keep_log, is_dir=False) is False
    assert parent.check(keep_log, is_dir=False) is True
    assert child.check(other_log, is_dir=False) is None
    assert parent.check(other_log, is_dir=False) is True
    assert (
        repo_map._stack_ignored(
            keep_log,
            is_dir=False,
            stack=(parent, child),
        )
        is False
    )
    assert (
        repo_map._stack_ignored(
            other_log,
            is_dir=False,
            stack=(parent, child),
        )
        is True
    )
