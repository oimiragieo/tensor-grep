"""Keep established gitignore matching semantics across dependency versions."""

from pathlib import Path

import pytest

from tensor_grep.cli.repo_map_gitignore import _GitignoreMatcher


@pytest.mark.parametrize(
    "rules,path,is_dir,expected",
    [
        (["!foo/bar", "foo/"], "foo/bar", False, True),
        (["!foo/bar", "foo/"], "foo/bar/item.py", False, True),
        (["foo/", "!foo/bar"], "foo/bar", False, False),
        (["!foo/bar/", "foo/"], "foo/bar/item.py", False, True),
        (["!foo/bar", "foo/*"], "foo/bar", False, True),
        (["*.log", "!sub/keep.log"], "sub/keep.log", False, False),
        (["*.log", "!sub/keep.log"], "sub/other.log", False, True),
        ([r"\!foo", "cache/"], "!foo", False, True),
        ([r"\#foo", "cache/"], "#foo", False, True),
        (["cache/"], "cache", True, True),
        (["[abc/"], "[abc/item.py", False, True),
        (["*", "![abc"], "[abc", False, False),
    ],
    ids=[
        "later-directory-overrides-file-negation",
        "later-directory-overrides-descendant-negation",
        "later-file-negation-reincludes",
        "later-directory-overrides-directory-negation",
        "later-glob-overrides-file-negation",
        "nested-file-negation-reincludes",
        "nested-negation-keeps-sibling-ignored",
        "escaped-bang-is-literal",
        "escaped-hash-is-literal",
        "directory-marker",
        "unclosed-class-directory-is-literal",
        "unclosed-class-negation-is-literal",
    ],
)
def test_gitignore_preserves_rule_order_and_literal_compatibility(
    tmp_path, rules, path, is_dir, expected
):
    matcher = _GitignoreMatcher(tmp_path, rules)
    assert matcher.check(Path(path), is_dir=is_dir) is expected


def test_bare_slash_is_noop_beside_valid_rule(tmp_path):
    matcher = _GitignoreMatcher(tmp_path, ["/", "*.log"])
    assert matcher.has_rules is True
    assert matcher.check(Path("ordinary.py"), is_dir=False) is None
    assert matcher.check(Path("sample.log"), is_dir=False) is True
