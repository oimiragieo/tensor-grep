from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


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
    ],
    ids=["duplicate-key", "duplicate-merge", "deep-nesting"],
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


def test_new_test_explicit_config_accepts_valid_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("language: json\ntestDirs: [checks]\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["new", "test", "probe", "--config", str(config_path), "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "checks" / "probe.yml").exists()
