from __future__ import annotations

import importlib.metadata as importlib_metadata
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap
from tensor_grep.cli.bootstrap import _KNOWN_COMMANDS
from tensor_grep.cli.commands import KNOWN_COMMANDS
from tensor_grep.cli.main import app


def test_bootstrap_commands_match_source_of_truth() -> None:
    assert _KNOWN_COMMANDS == set(KNOWN_COMMANDS), "Bootstrap commands must exactly match KNOWN_COMMANDS"

def test_typer_app_commands_match_source_of_truth() -> None:
    typer_commands = set()
    for cmd in app.registered_commands:
        typer_commands.add(cmd.name or cmd.callback.__name__)  # type: ignore
    for group in app.registered_groups:
        typer_commands.add(group.name)  # type: ignore

    expected_typer_cmds = {cmd for cmd in KNOWN_COMMANDS if not cmd.startswith("__")}
    assert typer_commands == expected_typer_cmds, "Typer commands must exactly match public KNOWN_COMMANDS"

def test_rust_core_uses_source_of_truth() -> None:
    rust_main = Path(__file__).resolve().parents[2] / "rust_core" / "src" / "main.rs"
    content = rust_main.read_text(encoding="utf-8")
    assert 'include_str!("../../src/tensor_grep/cli/commands.py")' in content, "Rust core must include commands.py as source of truth"


def test_resolve_native_tg_binary_should_prefer_env_override(monkeypatch, tmp_path):
    native_binary = tmp_path / "tg.exe"
    native_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(native_binary))

    resolved = bootstrap._resolve_native_tg_binary()

    assert resolved == str(native_binary)


def test_main_entry_should_passthrough_search_subcommand_to_rg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
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
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
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
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "_resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_delegate_tg_specific_flags_even_when_rust_first_env_is_enabled(
    monkeypatch,
):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--debug"])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native tg should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_delegate_cpu_flag_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--cpu"])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--cpu"]}


def test_main_entry_should_delegate_plain_search_to_native_tg_when_rust_first_env_is_enabled(
    monkeypatch,
):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["-i", "ERROR", "."]}


def test_main_entry_should_delegate_cpu_flag_to_env_override_native_tg(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    native_binary = tmp_path / "tg.exe"
    native_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--cpu"])
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(native_binary))
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": str(native_binary), "search_args": ["ERROR", ".", "--cpu"]}


def test_main_entry_should_delegate_ndjson_flag_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--ndjson"])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--ndjson"]}


def test_main_entry_should_delegate_multi_pattern_gpu_search_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tg",
            "search",
            "--gpu-device-ids",
            "0",
            "-e",
            "error",
            "-e",
            "warn",
            "-e",
            "fatal",
            "bench_data",
        ],
    )
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "binary_name": "tg.exe",
        "search_args": [
            "--gpu-device-ids",
            "0",
            "-e",
            "error",
            "-e",
            "warn",
            "-e",
            "fatal",
            "bench_data",
        ],
    }


def test_main_entry_should_fallback_to_full_cli_when_rg_is_unavailable(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
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
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_calibrate_subcommand(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "calibrate"])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_update_subcommand(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "update"])
    monkeypatch.setattr(bootstrap, "_resolve_native_tg_binary", lambda: None)
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
