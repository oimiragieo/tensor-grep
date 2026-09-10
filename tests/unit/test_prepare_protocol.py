"""RED-first tests for `prepare_protocol.ValidationAdvice` (AGT-03 remainder).

Covers construction of a typed `ValidationAdvice` for every allowlisted recipe in
`prepare_service._KNOWN_VALIDATION_RECIPES`, plus the unavailable/unrecognized case.
"""

from __future__ import annotations

from tensor_grep.cli import prepare_service
from tensor_grep.cli.prepare_protocol import ValidationAdvice, build_validation_advice


def test_validation_advice_is_frozen_dataclass_with_expected_fields() -> None:
    advice = build_validation_advice(["pytest -q"])
    assert isinstance(advice, ValidationAdvice)
    try:
        advice.status = "unavailable"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("ValidationAdvice must be frozen")


def test_pytest_q_recipe_is_available() -> None:
    advice = build_validation_advice(["pytest -q"])
    assert advice.status == "available"
    assert advice.argv == ("pytest", "-q")
    assert advice.source == "pytest -q"
    assert advice.reason is None


def test_bare_pytest_recipe_is_available() -> None:
    advice = build_validation_advice(["pytest"])
    assert advice.status == "available"
    assert advice.argv == ("pytest",)
    assert advice.source == "pytest"


def test_uv_run_pytest_q_recipe_is_available() -> None:
    advice = build_validation_advice(["uv run pytest -q"])
    assert advice.status == "available"
    assert advice.argv == ("uv", "run", "pytest", "-q")
    assert advice.source == "uv run pytest -q"


def test_uv_run_pytest_recipe_is_available() -> None:
    advice = build_validation_advice(["uv run pytest"])
    assert advice.status == "available"
    assert advice.argv == ("uv", "run", "pytest")
    assert advice.source == "uv run pytest"


def test_cargo_test_recipe_is_available() -> None:
    advice = build_validation_advice(["cargo test"])
    assert advice.status == "available"
    assert advice.argv == ("cargo", "test")
    assert advice.source == "cargo test"


def test_go_test_recipe_is_available() -> None:
    advice = build_validation_advice(["go test ./..."])
    assert advice.status == "available"
    assert advice.argv == ("go", "test", "./...")
    assert advice.source == "go test ./..."


def test_npm_test_recipe_is_available() -> None:
    advice = build_validation_advice(["npm test"])
    assert advice.status == "available"
    assert advice.argv == ("npm", "test")
    assert advice.source == "npm test"


def test_pnpm_test_recipe_is_available() -> None:
    advice = build_validation_advice(["pnpm test"])
    assert advice.status == "available"
    assert advice.argv == ("pnpm", "test")
    assert advice.source == "pnpm test"


def test_yarn_test_recipe_is_available() -> None:
    advice = build_validation_advice(["yarn test"])
    assert advice.status == "available"
    assert advice.argv == ("yarn", "test")
    assert advice.source == "yarn test"


def test_unrecognized_recipe_is_unavailable() -> None:
    advice = build_validation_advice(["make check-everything"])
    assert advice.status == "unavailable"
    assert advice.argv == ()
    assert advice.reason is not None
    assert "no allowlisted validation recipe" in advice.reason


def test_empty_validation_commands_is_unavailable() -> None:
    advice = build_validation_advice([])
    assert advice.status == "unavailable"
    assert advice.argv == ()


def test_first_match_wins_when_multiple_recipes_present() -> None:
    advice = build_validation_advice(["make check", "cargo test", "pytest -q"])
    assert advice.status == "available"
    assert advice.source == "cargo test"
    assert advice.argv == ("cargo", "test")


def test_build_validation_advice_reuses_prepare_service_allowlist() -> None:
    # Every allowlisted recipe in prepare_service must yield "available" here -- proof this
    # module delegates to the single allowlist rather than duplicating it.
    for command, argv in prepare_service._KNOWN_VALIDATION_RECIPES.items():
        advice = build_validation_advice([command])
        assert advice.status == "available"
        assert advice.argv == argv
