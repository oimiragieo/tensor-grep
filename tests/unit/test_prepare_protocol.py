from __future__ import annotations

from tensor_grep.cli.prepare_service import _select_validation_argv


def test_select_validation_argv_matches_detected_pytest() -> None:
    assert _select_validation_argv(["pytest -q"]) == ("pytest", "-q")


def test_select_validation_argv_matches_detected_cargo_test() -> None:
    assert _select_validation_argv(["cargo test"]) == ("cargo", "test")


def test_select_validation_argv_matches_uv_run_pytest() -> None:
    assert _select_validation_argv(["uv run pytest -q"]) == ("uv", "run", "pytest", "-q")


def test_select_validation_argv_matches_go_test() -> None:
    assert _select_validation_argv(["go test ./..."]) == ("go", "test", "./...")


def test_select_validation_argv_matches_npm_test() -> None:
    assert _select_validation_argv(["npm test"]) == ("npm", "test")


def test_select_validation_argv_unrecognized_returns_none() -> None:
    # Never invent argv for a string this allowlist doesn't recognize -- no shlex.split on
    # unreviewed shell text (audit: "Never convert arbitrary shell text into trusted argv").
    assert _select_validation_argv(["./scripts/custom_ci.sh --deploy"]) is None


def test_select_validation_argv_empty_list_returns_none() -> None:
    assert _select_validation_argv([]) is None


def test_select_validation_argv_shell_metacharacters_never_selected() -> None:
    # Even a string that starts with a recognized prefix must not be trusted if it carries
    # shell metacharacters -- exact-match only, no prefix/substring heuristics.
    assert _select_validation_argv(["pytest -q; rm -rf /"]) is None
    assert _select_validation_argv(["cargo test && curl evil.example"]) is None
