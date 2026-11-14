"""Preserve no-op rule normalization while adopting gitignore character-class support."""

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map_gitignore as owner


@pytest.mark.parametrize("lines", [[], ["# comment"], [" "], ["/"], ["  # indent"], ["!"]])
def test_noop_gitignore_rules_have_no_effective_rules(tmp_path, lines):
    matcher = owner._GitignoreMatcher(tmp_path, lines)
    assert matcher.has_rules is False
    assert matcher.check(Path("sample.log"), is_dir=False) is None


def test_bare_negation_does_not_discard_valid_sibling_rule(tmp_path):
    matcher = owner._GitignoreMatcher(tmp_path, ["!", "*.log"])
    assert matcher.has_rules is True
    assert matcher.check(Path("sample.log"), is_dir=False) is True
    assert matcher.check(Path("ordinary.py"), is_dir=False) is None


def test_valid_negation_still_reincludes_matching_file(tmp_path):
    matcher = owner._GitignoreMatcher(tmp_path, ["*.log", "!keep.log"])
    assert matcher.has_rules is True
    assert matcher.check(Path("other.log"), is_dir=False) is True
    assert matcher.check(Path("keep.log"), is_dir=False) is False


@pytest.mark.parametrize("rule,name", [(r"\#literal", "#literal"), (r"\!literal", "!literal")])
def test_escaped_comment_and_negation_are_literal_rules(tmp_path, rule, name):
    matcher = owner._GitignoreMatcher(tmp_path, [rule])
    assert matcher.has_rules is True
    assert matcher.check(Path(name), is_dir=False) is True
    assert matcher.check(Path("ordinary.py"), is_dir=False) is None
