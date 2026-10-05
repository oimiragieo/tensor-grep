from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


@pytest.fixture(autouse=True)
def _clear_root_cache():
    import importlib

    try:
        mod = importlib.import_module("tensor_grep.cli.repo_map_test_paths")
    except ModuleNotFoundError:  # RED on main: the module does not exist yet
        yield
        return
    mod._checkout_root.cache_clear()
    yield


@pytest.mark.parametrize(
    "rel",
    [
        "pkg/foo_test.go",
        "src/foo_test.rs",
        "src/test/java/Foo.java",
        "src/FooTest.java",
        "src/FooTests.cs",
        "src/FooTest.php",
        "web/a.test.tsx",
        "web/a.spec.mjs",
        "web/a.test.cjs",
        "pkg/test_x.py",
        "pkg/x_test.py",
        "web/__tests__/a.js",
    ],
)
def test_language_test_conventions_are_classified_as_tests(rel: str) -> None:
    assert repo_map._is_test_file(Path(rel)) is True


@pytest.mark.parametrize(
    "rel",
    [
        "src/latest.py",
        "src/contest.go",
        "src/attest.ts",
        "src/Contest.java",
        "web/a.tsx",
        "src/util.py",
    ],
)
def test_non_test_names_are_not_misclassified(rel: str) -> None:
    assert repo_map._is_test_file(Path(rel)) is False


def test_tests_directory_above_the_checkout_root_does_not_mark_sources_as_tests(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "tests" / "checkout"
    (checkout / ".git").mkdir(parents=True)
    src = checkout / "src" / "util.py"
    inner = checkout / "tests" / "unit" / "helper.py"
    for current in (src, inner):
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text("x = 1\n", encoding="utf-8")
    assert repo_map._is_test_file(src) is False
    assert repo_map._is_test_file(inner) is True


def test_without_a_git_root_the_ancestor_rule_is_unchanged(tmp_path: Path) -> None:
    # deliberate status-quo pin (not RED): no checkout root -> absolute-path parts rule as today
    loose = tmp_path / "tests" / "loose" / "util.py"
    loose.parent.mkdir(parents=True)
    loose.write_text("x = 1\n", encoding="utf-8")
    assert repo_map._is_test_file(loose) is True


def test_without_a_git_root_singular_test_dir_is_not_newly_a_test(tmp_path: Path) -> None:
    # council wave-2a r3 status-quo pin: main ignores singular `test`; a .git-less tree keeps that
    src = tmp_path / "test" / "app" / "x.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n", encoding="utf-8")
    assert repo_map._is_test_file(src) is False


def test_singular_test_dir_inside_a_checkout_is_a_test(tmp_path: Path) -> None:  # positive control
    (tmp_path / ".git").mkdir()
    src = tmp_path / "src" / "test" / "java" / "FooIT.java"
    src.parent.mkdir(parents=True)
    src.write_text("class FooIT {}\n", encoding="utf-8")
    assert repo_map._is_test_file(src) is True
