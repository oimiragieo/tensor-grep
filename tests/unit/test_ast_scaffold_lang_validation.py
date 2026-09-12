"""`tg ast new --lang` must not be able to inject YAML into the generated rule file.

`_write_ast_project_scaffold` hand-formats the sample rule as

    f"id: sample-rule\\nlanguage: {lang}\\nrule:\\n  pattern: 'print($$$ARGS)'\\n"

so a newline in `lang` writes sibling YAML keys of the attacker's choosing into a file the
user is then told is a valid scaffold. The sibling `name` parameter was already validated
(`_validate_ast_new_name`); `lang` was not.
"""

from __future__ import annotations

import pytest

from tensor_grep.cli.main import _write_ast_project_scaffold


@pytest.mark.parametrize(
    "hostile",
    [
        "python\nmalicious: true",
        "python\r\nid: not-sample-rule",
        "\npattern: $$$",
        "",
        "   ",
    ],
)
def test_newline_bearing_lang_is_refused(tmp_path, hostile: str) -> None:
    with pytest.raises(ValueError, match="Invalid --lang"):
        _write_ast_project_scaffold(tmp_path, hostile)


@pytest.mark.parametrize("good", ["python", "javascript", "rust", "c++", "c#", "csharp"])
def test_real_language_spellings_still_scaffold(tmp_path, good: str) -> None:
    """MUTATION CONTROL.

    A guard that rejected everything would pass the hostile cases above while breaking the
    feature. `c++` and `c#` are not Python identifiers, so an `isidentifier()`-style guard
    would wrongly refuse them -- this test fails if the guard over-rejects.
    """
    project_dir = tmp_path / good.replace("+", "p").replace("#", "sharp")
    project_dir.mkdir()

    config_path = _write_ast_project_scaffold(project_dir, good)

    assert config_path.exists()
    rule = (project_dir / "rules" / "sample-rule.yml").read_text(encoding="utf-8")
    assert f"language: {good}\n" in rule
    # The scaffold must stay a 3-key rule; nothing the language string carried may become a
    # sibling key.
    assert rule.count("\n") == 4, rule
