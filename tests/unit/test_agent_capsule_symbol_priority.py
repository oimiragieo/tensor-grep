from tensor_grep.cli.agent_capsule_targets import _prefer_public_implementation_over_private_helper


def test_prefer_public_implementation_over_private_helper() -> None:
    primary = {
        "file": "src/tensor_grep/cli/repo_map_lang_python.py",
        "symbol": "_add",
        "kind": "function",
        "confidence": 1.0,
    }
    alternatives = [
        {
            "file": "src/tensor_grep/core/retrieval.py",
            "symbol": "replace_with_retry",
            "kind": "function",
            "confidence": 0.85,
        }
    ]
    query = "add retry with tests"
    new_primary, new_alts = _prefer_public_implementation_over_private_helper(
        query, primary, alternatives
    )
    assert new_primary["symbol"] == "replace_with_retry"
    assert new_alts[0]["symbol"] == "_add"


def test_prefer_public_implementation_ignores_low_confidence_unrelated_alternatives() -> None:
    primary = {
        "file": "src/tensor_grep/cli/repo_map_lang_python.py",
        "symbol": "_add",
        "kind": "function",
        "confidence": 1.0,
    }
    # Low confidence or unrelated alternatives must NOT hijack the primary
    alternatives = [
        {
            "file": "src/tensor_grep/cli/irrelevant.py",
            "symbol": "unrelated_tool",
            "kind": "function",
            "confidence": 0.3,
        }
    ]
    query = "add retry with tests"
    new_primary, new_alts = _prefer_public_implementation_over_private_helper(
        query, primary, alternatives
    )
    assert new_primary["symbol"] == "_add"
    assert new_alts == alternatives
