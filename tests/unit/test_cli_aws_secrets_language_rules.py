import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tests.unit.test_cli_modes_shared import _FakeAstPipeline


@pytest.mark.parametrize(
    ("language", "extension", "content", "expected_rule_ids"),
    [
        pytest.param(
            "python",
            ".py",
            'access_key = "AKIAIOSFODNN7EXAMPLE"\naws_secret_access_key = "' + "A" * 40 + '"',
            {"python-aws-access-key-id", "python-aws-secret-access-key"},
            id="python",
        ),
        pytest.param(
            "javascript",
            ".js",
            'const accessKey = "AKIAIOSFODNN7EXAMPLE";\n'
            'const aws_secret_access_key = "' + "A" * 40 + '";',
            {"javascript-aws-access-key-id", "javascript-aws-secret-access-key"},
            id="javascript",
        ),
        pytest.param(
            "typescript",
            ".ts",
            'const accessKey = "AKIAIOSFODNN7EXAMPLE";\n'
            'const aws_secret_access_key = "' + "A" * 40 + '";',
            {"typescript-aws-access-key-id", "typescript-aws-secret-access-key"},
            id="typescript",
        ),
        pytest.param(
            "rust",
            ".rs",
            'let access_key = "AKIAIOSFODNN7EXAMPLE";\n'
            'let aws_secret_access_key = "' + "A" * 40 + '";',
            {"rust-aws-access-key-id", "rust-aws-secret-access-key"},
            id="rust",
        ),
    ],
)
def test_scan_secrets_basic_detects_aws_credentials_for_each_language(
    monkeypatch, language: str, extension: str, content: str, expected_rule_ids: set[str]
):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    fixture_path = f"fixture{extension}"

    class FixtureScanner:
        def __init__(self, config=None):
            pass

        def walk(self, path):
            yield fixture_path

    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", FixtureScanner)
    with CliRunner().isolated_filesystem():
        Path(fixture_path).write_text(content, encoding="utf-8")
        result = CliRunner().invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", language, "--path", ".", "--json"],
        )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    actual_rule_ids = {
        finding["rule_id"] for finding in payload["findings"] if finding["matches"] > 0
    }
    assert actual_rule_ids == expected_rule_ids


@pytest.mark.parametrize(
    ("language", "extension", "content"),
    [
        pytest.param(
            "python",
            ".py",
            'access_key = "AKIA1234"\naws_secret_access_key = os.environ["X"]',
            id="python",
        ),
        pytest.param(
            "javascript",
            ".js",
            'const accessKey = "AKIA1234";\n'
            "const aws_secret_access_key = process.env.AWS_SECRET_ACCESS_KEY;",
            id="javascript",
        ),
        pytest.param(
            "typescript",
            ".ts",
            'const accessKey: string = "AKIA1234";\n'
            "const aws_secret_access_key: string = process.env.AWS_SECRET_ACCESS_KEY;",
            id="typescript",
        ),
        pytest.param(
            "rust",
            ".rs",
            'let access_key = "AKIA1234";\n'
            'let aws_secret_access_key = std::env::var("AWS_SECRET_ACCESS_KEY");',
            id="rust",
        ),
    ],
)
def test_scan_secrets_basic_does_not_flag_non_aws_credentials_for_each_language(
    monkeypatch, language: str, extension: str, content: str
):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    fixture_path = f"fixture{extension}"

    class FixtureScanner:
        def __init__(self, config=None):
            pass

        def walk(self, path):
            yield fixture_path

    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", FixtureScanner)
    with CliRunner().isolated_filesystem():
        Path(fixture_path).write_text(content, encoding="utf-8")
        result = CliRunner().invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", language, "--path", ".", "--json"],
        )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0
