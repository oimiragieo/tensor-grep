import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.cli.rule_packs import list_rule_packs, resolve_rule_pack
from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner


def _patch_fake_ast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)


def _scan_ruleset_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ruleset: str,
    language: str,
    content: str,
    extra_args: list[str] | None = None,
) -> dict[str, object]:
    _patch_fake_ast(monkeypatch)
    monkeypatch.chdir(tmp_path)
    Path("a.py").write_text(content, encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    runner = CliRunner()
    args = [
        "scan",
        "--ruleset",
        ruleset,
        "--language",
        language,
        "--path",
        ".",
        "--json",
    ]
    if extra_args:
        args.extend(extra_args)

    result = runner.invoke(app, args)
    assert result.exit_code == 0
    return json.loads(result.output)


def test_list_rule_packs_includes_all_security_packs_with_expected_metadata() -> None:
    packs = {pack["name"]: pack for pack in list_rule_packs()}

    assert set(packs) == {
        "auth-safe",
        "crypto-safe",
        "deserialization-safe",
        "secrets-basic",
        "subprocess-safe",
        "tls-safe",
    }

    for name in ("auth-safe", "deserialization-safe", "subprocess-safe"):
        pack = packs[name]
        assert pack["category"] == "security"
        assert pack["status"] == "preview"
        assert pack["default_language"] == "python"
        assert pack["languages"] == ["javascript", "python", "rust", "typescript"]


@pytest.mark.parametrize(
    ("ruleset", "language", "minimum_rules", "maximum_rules"),
    [
        ("auth-safe", "python", 8, 12),
        ("auth-safe", "javascript", 8, 12),
        ("auth-safe", "typescript", 8, 12),
        ("auth-safe", "rust", 8, 12),
        ("deserialization-safe", "python", 6, 10),
        ("deserialization-safe", "javascript", 6, 10),
        ("deserialization-safe", "typescript", 6, 10),
        ("deserialization-safe", "rust", 6, 10),
        ("subprocess-safe", "python", 8, 12),
        ("subprocess-safe", "javascript", 8, 12),
        ("subprocess-safe", "typescript", 8, 12),
        ("subprocess-safe", "rust", 8, 12),
    ],
)
def test_resolve_rule_pack_exposes_new_multilanguage_rules(
    ruleset: str,
    language: str,
    minimum_rules: int,
    maximum_rules: int,
) -> None:
    metadata, rules = resolve_rule_pack(ruleset, language)

    assert metadata["name"] == ruleset
    assert metadata["category"] == "security"
    assert metadata["status"] == "preview"
    assert metadata["language"] == language
    assert minimum_rules <= len(rules) <= maximum_rules
    assert metadata["rule_count"] == len(rules)
    assert all(rule["language"] == language for rule in rules)
    assert all(rule["id"].startswith(f"{language}-") for rule in rules)
    assert all(rule["message"] for rule in rules)


@pytest.mark.parametrize(
    ("ruleset", "language", "content", "expected_rule_id"),
    [
        ("auth-safe", "python", "eval($$$ARGS)\n", "python-auth-eval"),
        ("auth-safe", "javascript", "eval($$$ARGS)\n", "javascript-auth-eval"),
        (
            "auth-safe",
            "typescript",
            'jwt.sign($PAYLOAD, "$SECRET")\n',
            "typescript-jwt-sign-hardcoded-secret",
        ),
        (
            "auth-safe",
            "rust",
            "rhai::Engine::new().eval($CODE)\n",
            "rust-rhai-engine-eval",
        ),
        (
            "deserialization-safe",
            "python",
            "pickle.loads($$$ARGS)\n",
            "python-pickle-loads",
        ),
        (
            "deserialization-safe",
            "javascript",
            "JSON.parse($INPUT)\n",
            "javascript-json-parse-untrusted",
        ),
        (
            "deserialization-safe",
            "typescript",
            "JSON.parse($INPUT)\n",
            "typescript-json-parse-untrusted",
        ),
        (
            "deserialization-safe",
            "rust",
            "bincode::deserialize($BYTES)\n",
            "rust-bincode-deserialize",
        ),
        (
            "subprocess-safe",
            "python",
            "subprocess.run($CMD, shell=True)\n",
            "python-subprocess-run-shell-true",
        ),
        (
            "subprocess-safe",
            "javascript",
            "child_process.exec($CMD)\n",
            "javascript-child-process-exec",
        ),
        (
            "subprocess-safe",
            "typescript",
            "execSync($CMD)\n",
            "typescript-exec-sync",
        ),
        (
            "subprocess-safe",
            "rust",
            'Command::new("sh")\n',
            "rust-command-new-sh",
        ),
    ],
)
def test_new_rule_packs_detect_findings_across_languages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ruleset: str,
    language: str,
    content: str,
    expected_rule_id: str,
) -> None:
    payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
    )

    assert payload["ruleset"] == ruleset
    assert payload["language"] == language
    assert payload["matched_rules"] >= 1
    assert payload["total_matches"] >= 1
    finding = next(
        finding for finding in payload["findings"] if finding["rule_id"] == expected_rule_id
    )
    assert finding["matches"] == 1
    assert finding["files"] == ["a.py"]
    assert finding["status"] == "new"


_CANONICAL_SECURITY_PACK_NAMES = {
    "auth-safe",
    "crypto-safe",
    "deserialization-safe",
    "secrets-basic",
    "subprocess-safe",
    "tls-safe",
}


# ---------------------------------------------------------------------------
# CEO#6(c): resolve-only 1:1 ruleset mental-model aliases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("secrets", "secrets-basic"),
        ("auth", "auth-safe"),
        ("crypto", "crypto-safe"),
        ("deserialize", "deserialization-safe"),
        ("deserialization", "deserialization-safe"),
        ("subprocess", "subprocess-safe"),
        ("tls", "tls-safe"),
        ("ssl", "tls-safe"),
    ],
)
def test_resolve_rule_pack_accepts_mental_model_aliases(alias: str, canonical: str) -> None:
    metadata, rules = resolve_rule_pack(alias)

    assert metadata["name"] == canonical
    assert metadata["category"] == "security"
    assert metadata["rule_count"] == len(rules)
    assert len(rules) > 0


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("SECRETS", "secrets-basic"),
        ("  Auth  ", "auth-safe"),
        ("TLS", "tls-safe"),
        ("Ssl", "tls-safe"),
    ],
)
def test_resolve_rule_pack_aliases_are_case_and_whitespace_insensitive(
    alias: str, canonical: str
) -> None:
    metadata, _rules = resolve_rule_pack(alias)

    assert metadata["name"] == canonical


def test_resolve_rule_pack_real_names_always_win_over_aliases() -> None:
    """Real pack names must resolve to themselves, unaffected by the alias table."""
    for name in sorted(_CANONICAL_SECURITY_PACK_NAMES):
        metadata, _rules = resolve_rule_pack(name)
        assert metadata["name"] == name


def test_list_rule_packs_does_not_leak_resolve_only_aliases() -> None:
    """GUARDRAIL: `list_rule_packs()` must expose exactly the 6 canonical names -- aliases are
    resolve-only and must never appear as if they were real, independently-listed packs."""
    names = {pack["name"] for pack in list_rule_packs()}

    assert names == _CANONICAL_SECURITY_PACK_NAMES

    for alias in (
        "auth",
        "secrets",
        "crypto",
        "tls",
        "ssl",
        "subprocess",
        "deserialize",
        "deserialization",
        "security",
    ):
        assert alias not in names


def test_resolve_rule_pack_unknown_name_still_raises_value_error() -> None:
    """NEGATIVE: a genuinely-unknown name is unaffected by the alias/category additions."""
    with pytest.raises(ValueError, match="Unknown built-in ruleset"):
        resolve_rule_pack("totally-not-a-real-ruleset")


def test_resolve_rule_pack_security_category_word_lists_the_six_packs() -> None:
    """`security` names a CATEGORY (all 6 built-in packs today), not one ruleset -- it must
    raise a smart, actionable error listing the real packs rather than silently picking one
    (or unioning all 6, which would be its own undesigned meta-pack)."""
    with pytest.raises(ValueError) as excinfo:
        resolve_rule_pack("security")

    message = str(excinfo.value)
    for name in sorted(_CANONICAL_SECURITY_PACK_NAMES):
        assert name in message


def test_deserialization_safe_does_not_flag_yaml_safe_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset="deserialization-safe",
        language="python",
        content="yaml.load($DATA, Loader=yaml.SafeLoader)\n",
    )

    assert payload["matched_rules"] == 0
    assert payload["total_matches"] == 0
    assert all(finding["status"] == "clear" for finding in payload["findings"])


@pytest.mark.parametrize(
    ("ruleset", "language", "content", "expected_rule_id"),
    [
        ("auth-safe", "python", "eval($$$ARGS)\n", "python-auth-eval"),
        (
            "deserialization-safe",
            "python",
            "pickle.loads($$$ARGS)\n",
            "python-pickle-loads",
        ),
        (
            "subprocess-safe",
            "python",
            "subprocess.run($CMD, shell=True)\n",
            "python-subprocess-run-shell-true",
        ),
    ],
)
def test_new_rule_packs_support_baselines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ruleset: str,
    language: str,
    content: str,
    expected_rule_id: str,
) -> None:
    initial_payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
    )
    fingerprint = next(
        finding["fingerprint"]
        for finding in initial_payload["findings"]
        if finding["rule_id"] == expected_rule_id
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-baseline",
                "ruleset": ruleset,
                "language": language,
                "fingerprints": [fingerprint],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
        extra_args=["--baseline", str(baseline_path)],
    )

    finding = next(
        finding for finding in payload["findings"] if finding["rule_id"] == expected_rule_id
    )
    assert finding["status"] == "existing"
    assert payload["baseline"]["existing_findings"] == 1
    assert payload["baseline"]["new_findings"] == 0


@pytest.mark.parametrize(
    ("ruleset", "language", "content", "expected_rule_id"),
    [
        ("auth-safe", "python", "eval($$$ARGS)\n", "python-auth-eval"),
        (
            "deserialization-safe",
            "python",
            "pickle.loads($$$ARGS)\n",
            "python-pickle-loads",
        ),
        (
            "subprocess-safe",
            "python",
            "subprocess.run($CMD, shell=True)\n",
            "python-subprocess-run-shell-true",
        ),
    ],
)
def test_new_rule_packs_support_suppressions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ruleset: str,
    language: str,
    content: str,
    expected_rule_id: str,
) -> None:
    initial_payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
    )
    fingerprint = next(
        finding["fingerprint"]
        for finding in initial_payload["findings"]
        if finding["rule_id"] == expected_rule_id
    )
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "fingerprints": [fingerprint],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
        extra_args=["--suppressions", str(suppressions_path)],
    )

    finding = next(
        finding for finding in payload["findings"] if finding["rule_id"] == expected_rule_id
    )
    assert finding["status"] == "suppressed"
    assert payload["suppressions"]["suppressed_findings"] == 1


@pytest.mark.parametrize(
    ("ruleset", "language", "content", "expected_rule_id", "expected_snippet"),
    [
        ("auth-safe", "python", "eval($$$ARGS)\n", "python-auth-eval", "eval($$$ARGS"),
        (
            "deserialization-safe",
            "python",
            "pickle.loads($$$ARGS)\n",
            "python-pickle-loads",
            "pickle.loads",
        ),
        (
            "subprocess-safe",
            "python",
            "subprocess.run($CMD, shell=True)\n",
            "python-subprocess-run-shell-true",
            "subprocess.r",
        ),
    ],
)
def test_new_rule_packs_emit_evidence_snippets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ruleset: str,
    language: str,
    content: str,
    expected_rule_id: str,
    expected_snippet: str,
) -> None:
    payload = _scan_ruleset_json(
        monkeypatch,
        tmp_path,
        ruleset=ruleset,
        language=language,
        content=content,
        extra_args=[
            "--include-evidence-snippets",
            "--max-evidence-snippets-per-file",
            "1",
            "--max-evidence-snippet-chars",
            "12",
        ],
    )

    finding = next(
        finding for finding in payload["findings"] if finding["rule_id"] == expected_rule_id
    )
    assert finding["evidence"] == [
        {
            "file": "a.py",
            "match_count": 1,
            "snippets": [{"text": expected_snippet, "truncated": True}],
        }
    ]
