import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tests.unit import test_cli_aws_secrets_language_rules as language_rules
from tests.unit import test_cli_modes_ast_backend as modes


@pytest.mark.parametrize(
    ("target", "content", "negative_line", "positive_line"),
    [
        pytest.param("modes", "key = 'AKIA1234'", 1, 2, id="short-access-key"),
        pytest.param("modes", "# rotate the AKIA key monthly", 1, 2, id="comment"),
        pytest.param(
            "language_rules",
            'access_key = "AKIA1234"\naws_secret_access_key = os.environ["X"]',
            1,
            3,
            id="language-short-access-key",
        ),
        pytest.param(
            "language_rules",
            'access_key = "AKIA1234"\naws_secret_access_key = os.environ["X"]',
            2,
            3,
            id="language-env-reference",
        ),
    ],
)
def test_secrets_negative_controls_reject_other_rule_findings(
    monkeypatch, target: str, content: str, negative_line: int, positive_line: int
):
    payload = {
        "findings": [
            {
                "rule_id": "python-aws-secret-access-key",
                "matches": 1,
                "occurrences": [{"line": positive_line}],
            },
            {
                "rule_id": "python-aws-access-key-id",
                "matches": 1,
                "occurrences": [{"line": negative_line}],
            },
        ]
    }

    def fake_invoke(_runner, *_args, **_kwargs):
        return SimpleNamespace(exit_code=0, stdout=json.dumps(payload))

    monkeypatch.setattr(CliRunner, "invoke", fake_invoke)
    with pytest.raises(AssertionError, match="secrets-basic findings on negative lines"):
        if target == "modes":
            modes.test_scan_secrets_basic_does_not_flag_non_credentials(monkeypatch, content, False)
        else:
            language_rules.test_scan_secrets_basic_does_not_flag_non_aws_credentials_for_each_language(
                monkeypatch, "python", ".py", content
            )
