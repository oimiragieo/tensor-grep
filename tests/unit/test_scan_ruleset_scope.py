import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _scan(path: Path, ruleset: str = "secrets-basic", *options: str):
    return CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            ruleset,
            "--path",
            str(path),
            "--json",
            "--allow-broad-generated-scan",
            *options,
        ],
    )


def _aws_source(language: str) -> str:
    prefix = "let " if language in {"rust", "typescript"} else ""
    return (
        f'{prefix}access_key = "AKIAIOSFODNN7EXAMPLE";\n'  # test fixture
        f'{prefix}aws_secret_access_key = "' + "A" * 40 + '";\n'
    )


def test_Scan_OmittedLanguageOnRustSecretsScope_FindsAwsRules(tmp_path: Path):
    rust_file = tmp_path / "main.rs"
    rust_file.write_text(
        'let access_key = "AKIAIOSFODNN7EXAMPLE";\n'  # test fixture
        'let aws_secret_access_key = "' + "A" * 40 + '";\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "secrets-basic",
            "--filter",
            "aws",
            "--path",
            str(tmp_path),
            "--json",
            "--allow-broad-generated-scan",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language"] == "rust"
    assert len(payload["findings"]) == 2
    assert payload["findings"][0]["rule_id"] == "rust-aws-access-key-id"
    assert payload["findings"][1]["rule_id"] == "rust-aws-secret-access-key"
    assert payload["findings"][0]["matches"] == 1
    assert payload["findings"][1]["matches"] == 1
    assert payload["total_matches"] == 2


def test_Scan_OmittedLanguageOnRustScope_SelectsRustAndFindsKnownCommand(tmp_path: Path):
    (tmp_path / "main.rs").write_text('let command = Command::new("sh");\n', encoding="utf-8")

    result = _scan(tmp_path, "subprocess-safe")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language"] == "rust"
    assert payload["languages"] == ["rust"]
    assert payload["findings"][0]["rule_id"] == "rust-command-new-sh"
    assert payload["findings"][0]["matches"] == 1


def test_Scan_MixedPythonRustScope_FindsBothLanguages(tmp_path: Path):
    (tmp_path / "main.py").write_text(_aws_source("python"), encoding="utf-8")
    (tmp_path / "main.rs").write_text(_aws_source("rust"), encoding="utf-8")

    result = _scan(tmp_path, "secrets-basic", "--filter", "aws")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language"] == "mixed"
    assert payload["languages"] == ["python", "rust"]
    assert payload["total_matches"] == 4


def test_Scan_ExplicitLanguageOnMixedScope_RestrictsRulesAndDisclosesScope(tmp_path: Path):
    (tmp_path / "main.py").write_text(_aws_source("python"), encoding="utf-8")
    (tmp_path / "main.rs").write_text(_aws_source("rust"), encoding="utf-8")

    result = _scan(tmp_path, "secrets-basic", "--language", "python", "--filter", "aws")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language"] == "python"
    assert payload["languages"] == ["python"]
    assert payload["scope"]["detected_languages"] == ["python", "rust"]
    assert payload["scope"]["files_considered"] == 2
    assert payload["scope"]["files_applicable"] == 1
    assert payload["total_matches"] == 2


def test_Scan_ExplicitPythonOnRustAstScope_RefusesWithoutClearFindings(tmp_path: Path):
    (tmp_path / "main.rs").write_text('let command = Command::new("sh");\n', encoding="utf-8")

    result = _scan(tmp_path, "subprocess-safe", "--language", "python")

    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "no_applicable_rules"
    assert payload["error"]["reason"] == "explicit_language_mismatch"
    assert payload["language"] == "python"
    assert payload["scope"]["detected_languages"] == ["rust"]
    assert payload["findings"] == []


def test_Scan_NoApplicableRules_TextWarnsWithoutCompletionClaim(tmp_path: Path):
    (tmp_path / "main.rs").write_text('let command = Command::new("sh");\n', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "subprocess-safe",
            "--language",
            "python",
            "--path",
            str(tmp_path),
            "--allow-broad-generated-scan",
        ],
    )

    assert result.exit_code == 1
    assert "No applicable rules were found" in result.stdout
    assert "Scan completed" not in result.stdout


def test_Scan_FilterSelectsRustRule_AppliesAfterLanguageUnion(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        'subprocess.run(["echo", "ok"], shell=True)\n', encoding="utf-8"
    )
    (tmp_path / "main.rs").write_text('let command = Command::new("sh");\n', encoding="utf-8")

    result = _scan(
        tmp_path,
        "subprocess-safe",
        "--filter",
        "rust-command-new-sh",
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language"] == "rust"
    assert payload["rule_count"] == 1
    assert payload["findings"][0]["rule_id"] == "rust-command-new-sh"


def test_Scan_FilterRemovesAllRules_WarnsAndReturnsNonzero(tmp_path: Path):
    (tmp_path / "main.rs").write_text('let command = Command::new("sh");\n', encoding="utf-8")

    result = _scan(tmp_path, "subprocess-safe", "--filter", "no-such-rule")

    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "no_applicable_rules"
    assert payload["error"]["reason"] == "filter_selected_no_rules"
    assert payload["rule_count"] == 0
    assert payload["findings"] == []


def test_Scan_EmptyScope_RefusesWithEmptyScopeReason(tmp_path: Path):
    result = _scan(tmp_path)

    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["reason"] == "empty_scope"
    assert payload["scope"]["files_considered"] == 0
    assert payload["findings"] == []


def test_Scan_ExcludedScope_RefusesWithEmptyScopeReason(tmp_path: Path):
    (tmp_path / "main.rs").write_text(
        'let access_key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )

    result = _scan(tmp_path, "secrets-basic", "--glob", "*.py", "--filter", "aws")

    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["reason"] == "empty_scope"
    assert payload["scope"]["files_considered"] == 0


@pytest.mark.parametrize("options", [(), ("--filter", "aws")])
def test_Scan_UnsupportedGoScope_RefusesWithNoApplicableFilesReason(tmp_path: Path, options):
    (tmp_path / "main.go").write_text('func main() { println("ok") }\n', encoding="utf-8")

    result = _scan(tmp_path, "secrets-basic", *options)

    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["reason"] == "no_applicable_files"
    assert payload["scope"]["detected_languages"] == ["go"]


def test_Scan_EnvRegexSecret_RemainsApplicableDespiteBashDetection(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\naws_secret_access_key="  # test fixture
        + "A" * 40
        + "\n",
        encoding="utf-8",
    )

    result = _scan(env_file, "secrets-basic", "--filter", "aws")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["language_selection"] == "default-regex-unknown"
    assert payload["scope"]["detected_languages"] == ["bash"]
    assert payload["total_matches"] == 2


def test_Scan_OmittedTsxRegexRules_UsesTypescriptEligibility(tmp_path: Path):
    (tmp_path / "component.tsx").write_text(
        _aws_source("typescript") + "export const View = () => <div />;\n", encoding="utf-8"
    )

    result = _scan(tmp_path, "secrets-basic", "--filter", "aws")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["scope"]["detected_languages"] == ["tsx"]
    assert payload["languages"] == ["typescript"]
    assert payload["findings"][0]["rule_id"] == "typescript-aws-access-key-id"
    assert payload["findings"][1]["rule_id"] == "typescript-aws-secret-access-key"
    assert payload["total_matches"] == 2
