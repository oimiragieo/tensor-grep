from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner


def _load_diagnostic():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "diagnose_gpu_delegation_route.py"
    )
    name = "gpu_delegation_route_diagnostic_test_subject"
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    original_path = sys.path.copy()
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


def _tiny_exit_app(exit_code: int) -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def command():
        if exit_code:
            raise typer.Exit(exit_code)

    return app


SUBJECT = _load_diagnostic()


PYTHON_MATCH_CASES = [
    pytest.param(0, id="complete"),
    pytest.param(1, id="not-found"),
]

INVALID_CONTROL_CASES = [
    pytest.param(
        {"exit_code": 2, "exception": None, "delegation_calls": []},
        {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]},
        id="retired-exit-two",
    ),
    pytest.param(
        {"exit_code": None, "exception": None, "delegation_calls": []},
        {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]},
        id="missing-exit",
    ),
    pytest.param(
        {"exit_code": 0, "exception": "RuntimeError()", "delegation_calls": []},
        {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]},
        id="exception",
    ),
    pytest.param(
        {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]},
        {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]},
        id="python-delegated",
    ),
    pytest.param(
        {"exit_code": 0, "exception": None, "delegation_calls": []},
        {"exit_code": 0, "exception": None, "delegation_calls": []},
        id="delegation-control-did-not-fire",
    ),
]

CLICK_EXIT_CASES = [
    pytest.param(0, type(None), None, id="normal-exit-zero"),
    pytest.param(1, SystemExit, 1, id="normal-exit-one"),
]

EXCEPTION_BOUNDARY_CASES = [
    pytest.param(1, SystemExit(2), "SystemExit(2)", id="system-exit-two"),
    pytest.param(1, SystemExit(0), "SystemExit(0)", id="mismatched-system-exit"),
    pytest.param(1, SystemExit("done"), "SystemExit('done')", id="string-exit"),
    pytest.param(1, SystemExit(True), "SystemExit(True)", id="boolean-exit-code"),
    pytest.param(1, RuntimeError("failure"), "RuntimeError('failure')", id="runtime-error"),
]


@pytest.mark.parametrize("exit_code", PYTHON_MATCH_CASES)
def test_ClassifyRoute_ForcedPythonExitZeroOrOne_ReportsControlMatch(exit_code):
    ambient = {"exit_code": exit_code, "exception": None, "delegation_calls": []}
    forced_python = {"exit_code": exit_code, "exception": None, "delegation_calls": []}
    forced_delegation = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}

    result = SUBJECT._classify_route_observation(ambient, forced_python, forced_delegation)

    assert result == "matches_forced_python"


def test_ClassifyRoute_AmbientDelegationObserved_ReportsDelegation():
    ambient = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}
    forced_python = {"exit_code": 0, "exception": None, "delegation_calls": []}
    forced_delegation = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}

    result = SUBJECT._classify_route_observation(ambient, forced_python, forced_delegation)

    assert result == "delegation_observed"


def test_ClassifyRoute_ExitTwoWithoutDelegation_ReportsNeither():
    ambient = {"exit_code": 2, "exception": None, "delegation_calls": []}
    forced_python = {"exit_code": 0, "exception": None, "delegation_calls": []}
    forced_delegation = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}

    result = SUBJECT._classify_route_observation(ambient, forced_python, forced_delegation)

    assert result == "neither"


def test_ClassifyRoute_AmbientExceptionWithMatchingExit_ReportsNeither():
    ambient = {"exit_code": 0, "exception": "RuntimeError()", "delegation_calls": []}
    forced_python = {"exit_code": 0, "exception": None, "delegation_calls": []}
    forced_delegation = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}

    result = SUBJECT._classify_route_observation(ambient, forced_python, forced_delegation)

    assert result == "neither"


@pytest.mark.parametrize(("forced_python", "forced_delegation"), INVALID_CONTROL_CASES)
def test_ClassifyRoute_InvalidOrNondiscriminatingControls_ReportsInvalid(
    forced_python, forced_delegation
):
    ambient = {"exit_code": 0, "exception": None, "delegation_calls": []}

    result = SUBJECT._classify_route_observation(ambient, forced_python, forced_delegation)

    assert result == "controls_invalid"


@pytest.mark.parametrize(("exit_code", "exception_type", "exception_code"), CLICK_EXIT_CASES)
def test_ObserveClick_NormalExitZeroOrOne_ReportsMatchingPythonControl(
    exit_code, exception_type, exception_code
):
    result = CliRunner().invoke(_tiny_exit_app(exit_code), [])

    diagnostic = SUBJECT._exception_diagnostic(result.exit_code, result.exception)
    ambient = {"exit_code": result.exit_code, "exception": diagnostic, "delegation_calls": []}
    forced_delegation = {"exit_code": 0, "exception": None, "delegation_calls": ["tg.exe"]}
    classification = SUBJECT._classify_route_observation(ambient, ambient, forced_delegation)

    assert result.exit_code == exit_code
    assert type(result.exception) is exception_type
    assert getattr(result.exception, "code", None) == exception_code
    assert classification == "matches_forced_python"
    assert diagnostic is None


@pytest.mark.parametrize(("exit_code", "exception", "expected"), EXCEPTION_BOUNDARY_CASES)
def test_DiagnoseException_NonmatchingSystemExitOrFailure_PreservesRepresentation(
    exit_code, exception, expected
):
    diagnostic = SUBJECT._exception_diagnostic(exit_code, exception)

    assert diagnostic == expected
