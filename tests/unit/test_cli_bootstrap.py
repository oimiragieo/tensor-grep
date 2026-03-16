import importlib.metadata as importlib_metadata
import sys

import pytest

from tensor_grep.cli import bootstrap


def test_main_entry_should_passthrough_search_subcommand_to_rg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["-i", "ERROR", "."]}


def test_main_entry_should_passthrough_raw_rg_style_invocation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["-i", "ERROR", "."]}


def test_main_entry_should_fallback_to_full_cli_for_tg_specific_flags(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--debug"])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_cpu_flag(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--cpu"])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_ndjson_flag(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--ndjson"])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_when_rg_is_unavailable(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_help(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "--help"])
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_route_scan_to_ast_workflow_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "scan", "--config", "sgconfig.yml"])
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: seen.update({"argv": list(argv)})
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    bootstrap.main_entry()

    assert seen == {"argv": ["scan", "--config", "sgconfig.yml"]}


def test_main_entry_should_route_run_to_ast_workflow_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "run", "ERROR", ".", "--lang", "python"])
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: seen.update({"argv": list(argv)})
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    bootstrap.main_entry()

    assert seen == {"argv": ["run", "ERROR", ".", "--lang", "python"]}


def test_main_entry_should_route_test_to_ast_workflow_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "test", "--config", "sgconfig.yml"])
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: seen.update({"argv": list(argv)})
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    bootstrap.main_entry()

    assert seen == {"argv": ["test", "--config", "sgconfig.yml"]}


def test_main_entry_should_print_version_without_loading_full_cli(monkeypatch, capsys):
    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(bootstrap, "_read_project_version_fallback", lambda: "9.9.9")
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert "tensor-grep 9.9.9" in capsys.readouterr().out
