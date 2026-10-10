import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tests.unit.aws_secrets_assertions import assert_no_negative_secret_lines
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
        positive_prefix = {
            "python": "aws_secret_access_key = ",
            "javascript": "const aws_secret_access_key = ",
            "typescript": "const aws_secret_access_key = ",
            "rust": "let aws_secret_access_key = ",
        }[language]
        positive_line = len(content.splitlines()) + 1
        Path(fixture_path).write_text(
            content + "\n" + positive_prefix + '"' + "A" * 40 + '"\n', encoding="utf-8"
        )
        Path("suppressions.json").write_text('{"entries": []}', encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                "scan",
                "--ruleset",
                "secrets-basic",
                "--language",
                language,
                "--path",
                ".",
                "--json",
                "--suppressions",
                "suppressions.json",
            ],
        )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    secret_finding = next(
        finding
        for finding in payload["findings"]
        if finding["rule_id"] == f"{language}-aws-secret-access-key"
    )
    assert secret_finding["matches"] == 1
    assert [occurrence["line"] for occurrence in secret_finding["occurrences"]] == [positive_line]
    assert_no_negative_secret_lines(payload, positive_line)


@pytest.mark.parametrize(
    ("language", "extension", "prefix", "suffix"),
    [
        pytest.param("python", ".py", "aws_secret_access_key = ", "", id="python"),
        pytest.param("javascript", ".js", "const aws_secret_access_key = ", ";", id="javascript"),
        pytest.param("typescript", ".ts", "const aws_secret_access_key = ", ";", id="typescript"),
        pytest.param("rust", ".rs", "let aws_secret_access_key = ", ";", id="rust"),
    ],
)
@pytest.mark.parametrize(
    "secret",
    ["A" * 39 + "=", "A" * 39 + "/", "A" * 19 + "+" + "A" * 20],
    ids=["trailing-equals", "trailing-slash", "contains-plus"],
)
@pytest.mark.parametrize("form", ["quoted", "unquoted", "quoted-eof", "unquoted-eof"])
def test_scan_secrets_basic_detects_punctuated_aws_secret_keys_for_each_language(
    monkeypatch, language: str, extension: str, prefix: str, suffix: str, secret: str, form: str
):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    fixture_path = f"fixture{extension}"

    class FixtureScanner:
        def __init__(self, config=None):
            pass

        def walk(self, path):
            yield fixture_path

    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", FixtureScanner)
    quoted = form.startswith("quoted")
    line = prefix + ('"' + secret + '"' if quoted else secret) + suffix
    content = line + ("" if form.endswith("eof") else "\n")
    with CliRunner().isolated_filesystem():
        Path(fixture_path).write_text(content, encoding="utf-8")
        result = CliRunner().invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", language, "--path", ".", "--json"],
        )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    secret_finding = next(
        finding
        for finding in payload["findings"]
        if finding["rule_id"] == f"{language}-aws-secret-access-key"
    )
    assert secret_finding["matches"] == 1
