from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _deep_merge_chain_yaml(depth: int) -> str:
    lines = ["l0: &l0 {language: python}"]
    for index in range(1, depth):
        lines.append(f"l{index}: &l{index} {{<<: *l{index - 1}}}")
    lines.append(f"<<: *l{depth - 1}")
    lines.append("language: python\n")
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        (
            "language: python\nlanguage: json\n",
            "cannot parse sgconfig.yml: duplicate key 'language'",
        ),
        (
            "first: &first {language: json}\nsecond: &second {severity: warning}\n"
            "<<: *first\n<<: *second\n",
            "cannot parse sgconfig.yml: duplicate key '<<'",
        ),
        (
            "extra: " + "[" * 600 + "0" + "]" * 600 + "\nlanguage: python\n",
            "cannot parse sgconfig.yml: invalid YAML syntax",
        ),
        (
            _deep_merge_chain_yaml(150),
            "cannot parse sgconfig.yml: invalid YAML syntax",
        ),
        (
            "language: on\n",
            'Cannot scaffold AST rule for language "on"; run `tg ast-info` for supported languages.',
        ),
        (
            "language: true\n",
            "Cannot scaffold AST rule for language true; run `tg ast-info` for supported languages.",
        ),
        (
            "language: 42\n",
            "Cannot scaffold AST rule for language 42; run `tg ast-info` for supported languages.",
        ),
        (
            "language: [python]\n",
            'Cannot scaffold AST rule for language ["python"]; run `tg ast-info` for supported languages.',
        ),
        (
            "language: 2024-01-01\n",
            'Cannot scaffold AST rule for language "2024-01-01"; run `tg ast-info` for supported languages.',
        ),
        (
            "42: value\nlanguage: python\n",
            "cannot parse sgconfig.yml: invalid YAML syntax",
        ),
        (
            "a: &a\n  <<: *a\n  language: python\n",
            "cannot parse sgconfig.yml: invalid YAML syntax",
        ),
    ],
    ids=[
        "duplicate-key",
        "duplicate-merge",
        "deep-nesting",
        "merge-chain-nesting",
        "boolean-language-on",
        "boolean-language-true",
        "numeric-language",
        "array-language",
        "date-language",
        "typed-key",
        "cyclic-merge",
    ],
)
def test_new_test_explicit_config_refuses_invalid_yaml_before_writing(
    tmp_path: Path, config_text: str, expected: str
) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text(config_text, encoding="utf-8")

    result = CliRunner().invoke(
        app, ["new", "test", "probe", "--config", str(config_path), "--yes"]
    )

    assert result.exit_code == 1, result.output
    assert result.output.strip() == f"Error: {expected}"
    assert not (tmp_path / "tests" / "probe.yml").exists()


@pytest.mark.parametrize("kind", ["rule", "util"])
def test_new_explicit_config_refuses_non_string_language_before_writing(
    tmp_path: Path, kind: str
) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("language: true\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["new", kind, "probe", "--config", str(config_path), "--yes"])

    assert result.exit_code == 1, result.output
    assert (
        "Cannot scaffold AST rule for language true; run `tg ast-info` for supported languages."
        in result.output
    )
    assert not (tmp_path / "rules" / "probe.yml").exists()
    assert not (tmp_path / "utils" / "probe.yml").exists()


def test_new_test_explicit_config_accepts_valid_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("language: json\ntestDirs: [checks]\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["new", "test", "probe", "--config", str(config_path), "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "checks" / "probe.yml").exists()
