import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli.main import (
    _select_ast_backend_for_pattern,
    app,
)
from tensor_grep.core.config import SearchConfig
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_ast_selection_should_skip_pipeline_for_native_backend(monkeypatch):
    # delete-dead-lsp-tensor-gnn: native is now reachable ONLY when ast-grep is ABSENT
    # (the wrapper is preferred whenever available, since the two backends use different
    # query DSLs and are not results-interchangeable). This test's real purpose is that a
    # DIRECT native selection skips Pipeline construction, so it now models the ast-grep-absent
    # fallback: wrapper unavailable + native available + native-shaped pattern -> native.
    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeDirectNativeAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeUnavailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.core.pipeline.Pipeline.__init__",
        lambda self, force_cpu=False, config=None: (_ for _ in ()).throw(
            AssertionError("Pipeline construction should be skipped for direct AST selection")
        ),
    )

    backend = _select_ast_backend_for_pattern(
        SearchConfig(query_pattern="function_definition", ast=True, ast_prefer_native=True),
        "function_definition",
        {},
    )

    assert isinstance(backend, _FakeDirectNativeAstBackend)


def test_ast_selection_should_skip_pipeline_for_wrapper_backend(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeDirectNativeAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeDirectWrapperAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.core.pipeline.Pipeline.__init__",
        lambda self, force_cpu=False, config=None: (_ for _ in ()).throw(
            AssertionError("Pipeline construction should be skipped for direct AST selection")
        ),
    )

    backend = _select_ast_backend_for_pattern(
        SearchConfig(query_pattern="def $FUNC():", ast=True, ast_prefer_native=True),
        "def $FUNC():",
        {},
    )

    assert isinstance(backend, _FakeDirectWrapperAstBackend)


def test_test_command_should_use_total_file_contract_for_match_detection(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeCountOnlyAstPipeline)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=2" in result.output


def test_devices_command_reports_no_gpu_when_none_detected(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _NO_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices"])

    assert result.exit_code == 0
    assert "No routable GPUs detected." in result.output


def test_devices_command_json_outputs_routable_device_inventory(monkeypatch):
    import json

    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 2
    assert payload["routable_device_ids"] == [7, 3]
    assert payload["devices"] == [
        {"device_id": 7, "vram_capacity_mb": 12288},
        {"device_id": 3, "vram_capacity_mb": 24576},
    ]


def test_devices_command_text_outputs_device_lines(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices"])

    assert result.exit_code == 0
    assert "Detected 2 routable GPU(s):" in result.output
    assert "- gpu:7 vram_mb=12288" in result.output
    assert "- gpu:3 vram_mb=24576" in result.output


def test_devices_command_format_json_outputs_inventory(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["platform"] == "windows"
    assert payload["device_count"] == 2


def test_devices_command_should_fail_on_unsupported_format(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--format", "xml"])

    assert result.exit_code == 2
    assert "must be one of: text, json" in result.output


def test_rule_test_command_executes_valid_and_invalid_cases(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/no_bad.yml").write_text(
            "id: no-bad\nlanguage: python\nrule:\n  pattern: BAD\n",
            encoding="utf-8",
        )
        Path("tests/no_bad_test.yml").write_text(
            (
                "tests:\n"
                "  - id: no-bad-basic\n"
                "    ruleId: no-bad\n"
                "    valid:\n"
                "      - 'all good'\n"
                "    invalid:\n"
                "      - 'contains BAD token'\n"
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=2" in result.output


def test_new_rule_uses_configured_rule_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text(
        "ruleDirs:\n  - custom-rules\ntestDirs:\n  - custom-tests\nutilsDir: custom-utils\n"
        "language: python\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["new", "rule", "demo", "--config", str(config_path), "--lang", "python", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "custom-rules" / "demo.yml").exists()
    assert not (tmp_path / "rules" / "demo.yml").exists()


def test_run_update_all_aliases_apply_for_rewrite(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($A)",
            "--rewrite",
            "logger.info($A)",
            "--update-all",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["pattern"] == "print($A)"
    assert seen["path"] == str(tmp_path)
    assert seen["kwargs"]["apply"] is True


def test_run_ast_grep_semantic_flags_are_forwarded_to_run_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($A)",
            "--selector",
            "call",
            "--strictness",
            "relaxed",
            "--globs",
            "*.py",
            "src",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["pattern"] == "print($A)"
    assert seen["path"] == "src"
    assert seen["kwargs"]["selector"] == "call"
    assert seen["kwargs"]["strictness"] == "relaxed"
    assert seen["kwargs"]["globs"] == ["*.py"]


def test_run_ast_grep_semantic_rewrite_combinations_fail_explicitly() -> None:
    result = CliRunner().invoke(
        app,
        ["run", "--pattern", "print($A)", "--selector", "call", "--rewrite", "logger.info($A)"],
    )

    assert result.exit_code == 1
    assert "ast-grep semantic run options are read-only" in result.output


def test_main_entry_should_not_rewrite_devices_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "devices", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "devices", "--json"]


def test_main_entry_should_disable_click_windows_arg_expansion_for_globs(monkeypatch):

    seen: dict[str, object] = {}

    def _fake_app(*_args, **kwargs):
        seen["argv"] = list(sys.argv)
        seen["kwargs"] = dict(kwargs)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--json", "--glob", "src/tensor_grep/cli/**", "-e", "needle", "."],
    )

    cli_main.main_entry()

    assert seen["argv"] == [
        "tg",
        "search",
        "--json",
        "--glob",
        "src/tensor_grep/cli/**",
        "-e",
        "needle",
        ".",
    ]
    assert seen["kwargs"]["windows_expand_args"] is False
    assert seen["kwargs"]["prog_name"] == "tg"


def test_main_entry_should_not_rewrite_map_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "map", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "map", "--json"]


def test_main_entry_should_not_rewrite_doctor_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "doctor", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "doctor", "--json"]


def test_main_entry_should_not_rewrite_checkpoint_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "checkpoint", "list"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "checkpoint", "list"]


def test_checkpoint_undo_existing_path_reports_last_hint_json(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()

    result = CliRunner().invoke(app, ["checkpoint", "undo", str(project), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "checkpoint_not_found"
    assert payload["checkpoint_id"] == str(project)
    assert payload["path"] == "."
    assert "parsed as CHECKPOINT_ID" in payload["detail"]
    assert "tg checkpoint undo --last" in payload["detail"]


def test_main_entry_should_not_rewrite_dogfood_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "dogfood", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "dogfood", "--json"]


def test_main_entry_should_not_rewrite_session_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "session", "list"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "session", "list"]


def test_main_entry_should_not_rewrite_calibrate_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "calibrate"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "calibrate"]


def test_main_entry_should_not_rewrite_top_level_help(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "--help"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "--help"]


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Rich legacy Windows pipe workaround is Windows-specific.",
)
def test_main_module_import_should_not_disable_rich_as_a_side_effect():
    # Regression guard for the "order-dependent help flake" (docs/BACKLOG.md): merely importing
    # tensor_grep.cli.main -- which many unrelated modules do, for helper symbols -- must never
    # mutate process-wide TYPER_USE_RICH. That mutation belongs to main_entry() only, scoped to
    # the actual CLI invocation, not to whichever module happens to import this one first.
    env = dict(os.environ)
    env.pop("TYPER_USE_RICH", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import os; import tensor_grep.cli.main; print(os.environ.get('TYPER_USE_RICH'))"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "None"


def test_main_entry_should_disable_rich_when_windows_stdout_is_redirected(monkeypatch):
    monkeypatch.delenv("TYPER_USE_RICH", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(cli_main, "app", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["tg"])

    cli_main.main_entry()

    assert os.environ.get("TYPER_USE_RICH") == "0"


def test_main_entry_should_not_rewrite_empty_argv(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg"]


def test_bootstrap_main_entry_should_route_scan_ruleset_through_full_cli(monkeypatch):
    from tensor_grep.cli import bootstrap as cli_bootstrap

    seen: dict[str, object] = {}

    def _fake_full_cli() -> None:
        seen["full_cli"] = True

    def _fake_ast_workflow_cli(argv: list[str]) -> None:
        seen["ast_workflow_argv"] = list(argv)

    monkeypatch.setattr(cli_bootstrap, "_run_full_cli", _fake_full_cli)
    monkeypatch.setattr(cli_bootstrap, "_run_ast_workflow_cli", _fake_ast_workflow_cli)
    monkeypatch.setattr(sys, "argv", ["tg", "scan", "--ruleset", "auth-safe"])

    cli_bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_bootstrap_run_help_should_not_expose_config_option(monkeypatch, capsys):
    from tensor_grep.cli import bootstrap as cli_bootstrap

    monkeypatch.setattr(sys, "argv", ["tg", "run", "--help"])

    with pytest.raises(SystemExit):
        cli_bootstrap.main_entry()

    help_text = capsys.readouterr().out
    assert "--config" not in help_text


def test_full_cli_run_help_should_not_expose_config_option() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "--config" not in help_text
    assert "--pattern" in help_text
    assert "--files-with-matches" in help_text


def test_full_cli_run_accepts_ast_grep_pattern_option(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "class $NAME: $$$BODY",
            "--files-with-matches",
            str(tmp_path),
            "--lang",
            "python",
        ],
    )

    assert result.exit_code == 0
    assert seen["pattern"] == "class $NAME: $$$BODY"
    assert seen["path"] == str(tmp_path)
    assert seen["kwargs"]["files_with_matches"] is True


def test_app_help_should_expose_the_python_public_top_level_surface():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    for snippet in TOP_LEVEL_HELP_REQUIRED_SNIPPETS:
        assert snippet in help_text
    normalized_help = re.sub(r"\s+", " ", _strip_ansi(result.stdout))
    assert (
        "Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries."
        in normalized_help
    )
    assert "tg doctor --with-lsp" in result.stdout
    assert "doctor" in result.stdout
    assert "symbol" in result.stdout


def test_search_help_should_render_python_search_help_smoke() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    for snippet in SEARCH_HELP_REQUIRED_SNIPPETS:
        assert snippet in help_text
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "multi-project workspace roots" in normalized_help


def test_worker_help_should_render_dedicated_hidden_command_help() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "--help"])
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "Resident AST Worker" in help_text
    assert "--port" in help_text
    assert "--stop" in help_text


def test_update_alias_calls_upgrade(monkeypatch) -> None:
    seen = {"called": False}

    def _fake_upgrade() -> None:
        seen["called"] = True

    monkeypatch.setattr("tensor_grep.cli.main.upgrade", _fake_upgrade)

    runner = CliRunner()
    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert seen["called"] is True


def test_audit_verify_json_reports_valid_signed_manifest(tmp_path):
    runner = CliRunner()
    manifest_path = tmp_path / "rewrite-audit.json"
    signing_key_path = tmp_path / "audit.key"
    signing_key = b"top-secret"
    signing_key_path.write_bytes(signing_key)
    payload = _write_audit_manifest(manifest_path, signing_key=signing_key)

    result = runner.invoke(
        app,
        [
            "audit-verify",
            str(manifest_path),
            "--signing-key",
            str(signing_key_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["routing_reason"] == "audit-manifest-verify"
    assert parsed["manifest_sha256"] == payload["manifest_sha256"]
    assert parsed["checks"] == {
        "digest_valid": True,
        "chain_valid": True,
        "signature_valid": True,
    }
    assert parsed["valid"] is True
    assert parsed["errors"] == []


def test_audit_history_json_lists_manifests_newest_first_and_updates_index(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    first_payload = _write_audit_manifest(
        audit_dir / "first.json",
        previous_manifest_sha256=None,
    )
    second_payload = _write_audit_manifest(
        audit_dir / "second.json",
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
    )

    result = runner.invoke(app, ["audit-history", str(project), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-history")
    assert [entry["manifest_sha256"] for entry in parsed["history"]] == [
        second_payload["manifest_sha256"],
        first_payload["manifest_sha256"],
    ]
    index_path = project / ".tensor-grep" / "audit" / "index.json"
    assert index_path.exists()


def test_audit_history_json_returns_empty_array_for_empty_audit_directory(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    (project / ".tensor-grep" / "audit").mkdir(parents=True)

    result = runner.invoke(app, ["audit-history", str(project), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-history")
    assert parsed["history"] == []


def test_audit_diff_json_reports_added_removed_and_changed_fields(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_payload = _write_audit_manifest(
        right_path,
        previous_manifest_sha256="f" * 64,
    )
    parsed_right = json.loads(right_path.read_text(encoding="utf-8"))
    parsed_right["kind"] = "rewrite-plan-manifest"
    parsed_right["reviewer"] = "alice"
    parsed_right["files"][0]["after_sha256"] = "c" * 64
    parsed_right["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(parsed_right)
    ).hexdigest()
    right_path.write_text(json.dumps(parsed_right, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-diff")
    assert parsed["added"] == {"reviewer": "alice"}
    assert parsed["removed"] == {}
    assert parsed["changed"] == {
        "kind": {"old": "rewrite-audit-manifest", "new": "rewrite-plan-manifest"},
        "files[0].after_sha256": {"old": "b" * 64, "new": "c" * 64},
        "previous_manifest_sha256": {"old": None, "new": "f" * 64},
    }
    assert right_payload["manifest_sha256"] != parsed_right["manifest_sha256"]


def test_audit_diff_default_output_is_human_readable(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    parsed_right = _write_audit_manifest(right_path)
    parsed_right["reviewer"] = "alice"
    parsed_right["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(parsed_right)
    ).hexdigest()
    right_path.write_text(json.dumps(parsed_right, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path)])

    assert result.exit_code == 0
    assert "Audit diff:" in result.stdout
    assert "Added" in result.stdout
    assert "reviewer" in result.stdout
    assert "Changed" in result.stdout


def test_audit_diff_json_returns_empty_sections_for_identical_manifests(tmp_path):
    runner = CliRunner()
    manifest_path = tmp_path / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)

    result = runner.invoke(app, ["audit-diff", str(manifest_path), str(manifest_path), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-diff")
    assert parsed["added"] == {}
    assert parsed["removed"] == {}
    assert parsed["changed"] == {}


def test_audit_diff_json_reports_not_found_error(tmp_path):
    runner = CliRunner()
    missing_left = tmp_path / "missing-left.json"
    missing_right = tmp_path / "missing-right.json"

    result = runner.invoke(app, ["audit-diff", str(missing_left), str(missing_right), "--json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "not_found"
    assert "Audit manifest not found" in parsed["error"]["message"]


def test_audit_diff_json_reports_invalid_json_error(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_path.write_text("{not valid json", encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path), "--json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "invalid_json"


def test_audit_verify_json_reports_chain_failure(tmp_path):
    runner = CliRunner()
    previous_manifest_path = tmp_path / "previous-audit.json"
    previous_payload = _write_audit_manifest(previous_manifest_path)
    wrong_previous = "f" * 64
    manifest_path = tmp_path / "rewrite-audit.json"
    _write_audit_manifest(manifest_path, previous_manifest_sha256=wrong_previous)

    result = runner.invoke(
        app,
        [
            "audit-verify",
            str(manifest_path),
            "--previous-manifest",
            str(previous_manifest_path),
            "--json",
        ],
    )

    # H1: audit-verify --json exits 1 when valid:false
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["checks"]["digest_valid"] is True
    assert parsed["checks"]["chain_valid"] is False
    assert parsed["checks"]["signature_valid"] is True
    assert parsed["valid"] is False
    assert "Previous manifest digest does not match previous_manifest_sha256." in parsed["errors"]
    assert parsed["previous_manifest_sha256"] == wrong_previous
    assert previous_payload["manifest_sha256"] != wrong_previous


def test_review_bundle_create_json_packages_artifacts_and_writes_bundle_file(tmp_path):
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")

    previous_path = audit_dir / "previous.json"
    previous_payload = _write_audit_manifest(previous_path, project_root=project)
    current_path = audit_dir / "current.json"
    _write_audit_manifest(
        current_path,
        previous_manifest_sha256=str(previous_payload["manifest_sha256"]),
        project_root=project,
    )
    scan_path = project / "scan.json"
    scan_payload = _write_scan_results(scan_path)
    checkpoint = create_checkpoint(str(project))
    bundle_path = tmp_path / "review-bundle.json"

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(current_path),
            "--scan",
            str(scan_path),
            "--checkpoint-id",
            checkpoint.checkpoint_id,
            "--previous-manifest",
            str(previous_path),
            "--output",
            str(bundle_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "review-bundle-create"
    assert payload["scan_results"] == scan_payload
    assert payload["checkpoint_metadata"]["checkpoint_id"] == checkpoint.checkpoint_id
    assert payload["diff"]["changed"]["previous_manifest_sha256"] == {
        "old": None,
        "new": previous_payload["manifest_sha256"],
    }
    assert json.loads(bundle_path.read_text(encoding="utf-8")) == payload


def test_review_bundle_verify_json_reports_invalid_integrity(tmp_path):
    from tensor_grep.cli import audit_manifest as audit_manifest_module

    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest_module.create_review_bundle(manifest_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["bundle_sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["review-bundle", "verify", str(bundle_path), "--json"])

    # H1: review-bundle verify --json exits 1 when valid:false
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "review-bundle-verify"
    assert payload["checks"]["audit_manifest"]["valid"] is True
    assert payload["bundle_integrity"]["valid"] is False
    assert payload["valid"] is False


def test_calibrate_command_delegates_to_native_tg(monkeypatch):

    seen: dict[str, object] = {}

    class _Completed:
        returncode = 0

    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check=False: seen.update({"cmd": list(cmd), "check": check}) or _Completed(),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["calibrate"])

    assert result.exit_code == 0
    assert seen == {"cmd": ["tg.exe", "calibrate"], "check": False}


def test_calibrate_command_json_flag_forwards_to_native_tg(monkeypatch):
    # v20 dogfood (GPU honesty / harness-misread): --json is additive -- it must not change
    # the argv tg.exe sees other than appending "--json", and it must not swallow the native
    # binary's real exit code (still 2 on a CPU-only no-cuda skip, per the fail-closed
    # backend-unavailable convention pinned by crossover.rs -- KEPT, not flipped to 0).
    seen: dict[str, object] = {}

    class _Completed:
        returncode = 2

    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check=False: seen.update({"cmd": list(cmd), "check": check}) or _Completed(),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["calibrate", "--json"])

    assert result.exit_code == 2
    assert seen == {"cmd": ["tg.exe", "calibrate", "--json"], "check": False}


def test_main_entry_should_rewrite_raw_pattern_to_search_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "ERROR", "."])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "search", "ERROR", "."]


def test_main_entry_should_fallback_to_pyproject_version_when_metadata_missing(monkeypatch, capsys):
    import importlib.metadata as importlib_metadata

    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "0.31.4")

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "tensor-grep 0.31.4\n"


def test_main_entry_should_keep_verbose_version_details_when_requested(monkeypatch, capsys):
    import importlib.metadata as importlib_metadata

    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version", "--verbose"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "0.31.4")

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    output = capsys.readouterr().out
    assert excinfo.value.code == 0
    assert output.startswith("tensor-grep 0.31.4\n\n")
    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in output
    assert "Arrow Zero-Copy IPC is available" in output


def test_main_entry_should_delegate_top_level_pcre2_version_to_native_binary(
    monkeypatch, tmp_path: Path, capsys
):

    native_binary = tmp_path / ("tg.exe" if sys.platform.startswith("win") else "tg")
    native_binary.write_text("binary", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_run(cmd, capture_output, text):
        seen["cmd"] = list(cmd)
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="PCRE2 10.42 is available (JIT is available)\n",
            stderr="",
        )

    monkeypatch.setattr(sys, "argv", ["tg", "--pcre2-version"])
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "cmd": [str(native_binary), "--pcre2-version"],
        "capture_output": True,
        "text": True,
    }
    assert "PCRE2 10.42" in capsys.readouterr().out


def test_main_entry_should_fail_pcre2_version_when_no_backend_is_available(monkeypatch, capsys):

    monkeypatch.setattr(sys, "argv", ["tg", "--pcre2-version"])
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "PCRE2 version unavailable" in captured.err


def test_tg_test_uses_typer_help():
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["test", "--help"])
    assert result.exit_code == 0
    assert "Usage: " in result.stdout
    assert "Options" in result.stdout.lower() or "options" in result.stdout.lower()
    assert "positional arguments:" not in result.stdout


def test_evidence_group_hints_emit_when_given_a_path_like_subcommand(tmp_path: Path) -> None:
    # Dogfood trap (v1.61.2): an agent reaches for `tg evidence <PATH> <query>` by analogy
    # with `tg defs`/`tg orient` (which take a path directly), but `evidence` is a command
    # GROUP whose only action is `emit`. The error stays exit 2 (correct) but must now nudge
    # the caller toward `emit` so they do not have to re-read --help.
    src = tmp_path / "main.py"
    src.write_text("x = 1\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["evidence", str(src), "search"])
    assert result.exit_code == 2
    combined = result.output
    assert "No such command" in combined
    assert "tg evidence emit" in combined


def test_evidence_group_does_not_hint_emit_for_a_non_path_subcommand() -> None:
    # A bare non-path token (a genuine subcommand typo) gets the standard error only -- the
    # emit hint must not fire on every mistyped subcommand, just the path-shaped ones.
    result = CliRunner().invoke(app, ["evidence", "boguscmd"])
    assert result.exit_code == 2
    combined = result.output
    assert "No such command" in combined
    assert "tg evidence emit" not in combined


def test_doctor_installation_health_foreign_wins() -> None:
    routes = [
        {
            "route": "path",
            "path": "/c/bin/tg.exe",
            "version": "tg 0.32.0",
            "foreign": True,
            "version_matches": False,
        },
    ]
    assert (
        cli_main._doctor_installation_health(
            routes, installed_version="9.9.9", installed_behind_pypi=False, pypi_unavailable=False
        )
        == "foreign_launcher"
    )


def test_doctor_installation_health_version_mismatch_affects_health() -> None:
    # A shadowed OLD tg (foreign already False here, but version_matches False) must NOT read ok.
    routes = [
        {
            "route": "path",
            "path": "/c/bin/tg.exe",
            "version": "tg 1.110.10",
            "foreign": False,
            "version_matches": False,
        },
    ]
    assert (
        cli_main._doctor_installation_health(
            routes,
            installed_version="1.110.13",
            installed_behind_pypi=False,
            pypi_unavailable=False,
        )
        == "launcher_version_mismatch"
    )


def test_doctor_installation_health_stale_install() -> None:
    assert (
        cli_main._doctor_installation_health(
            [], installed_version="1.110.10", installed_behind_pypi=True, pypi_unavailable=False
        )
        == "stale_install"
    )


def test_doctor_installation_health_unknown_pypi_when_probe_unavailable() -> None:
    assert (
        cli_main._doctor_installation_health(
            [], installed_version="1.110.13", installed_behind_pypi=None, pypi_unavailable=True
        )
        == "unknown_pypi"
    )


def test_doctor_installation_health_ok_when_clean_semantically_newer() -> None:
    # Clean routes + installed equal or newer than pypi -> ok.
    assert (
        cli_main._doctor_installation_health(
            [], installed_version="1.110.13", installed_behind_pypi=False, pypi_unavailable=False
        )
        == "ok"
    )


def test_doctor_installed_behind_pypi_semantic() -> None:
    # Semantic, NOT lexicographic: 1.110.10 < 1.110.9 is False (1.110.9 has fewer patch digits
    # but the strict padded dotted-numeric comparison yields 1.110.10 > 1.110.9). Use a clear
    # numeric case. (No `packaging` dependency — a strict padded tuple parser per plan REV 6.)
    assert cli_main._doctor_installed_behind_pypi("1.110.9", "1.110.10") is True
    assert cli_main._doctor_installed_behind_pypi("1.110.10", "1.110.10") is False
    assert cli_main._doctor_installed_behind_pypi("2.0.0", "1.110.13") is False


def test_doctor_installed_behind_pypi_null_on_invalid_or_unavailable() -> None:
    assert cli_main._doctor_installed_behind_pypi("junk", "1.110.13") is None
    assert cli_main._doctor_installed_behind_pypi("1.110.13", "junk") is None
    assert cli_main._doctor_installed_behind_pypi("1.110.13", None) is None


def test_doctor_shadow_launchers_inclusion_and_nulls() -> None:
    # foreign True -> listed; version_matches False -> listed; None (invalid route version) ->
    # listed; expected-version route -> NOT listed; absent route -> not present at all.
    routes = [
        {
            "route": "path",
            "path": "/c/bin/other.tg",
            "version": "tg 0.32.0",
            "foreign": True,
            "version_matches": False,
        },
        {
            "route": "fresh_shell_path",
            "path": "/c/bin/old-tg.exe",
            "version": "tg 1.110.10",
            "foreign": False,
            "version_matches": False,
        },
        {
            "route": "python_subprocess_path",
            "path": "/c/bin/junk.tg",
            "version": "??",
            "foreign": False,
            "version_matches": None,
        },
    ]
    # Include a good route that must NOT be listed.
    good = {
        "route": "path",
        "path": "/c/.tensor-grep/bin/tg.exe",
        "version": "tg 1.110.13",
        "foreign": False,
        "version_matches": True,
    }
    shadow = cli_main._doctor_shadow_launchers([*routes, good])
    listed_routes = {entry["route"] for entry in shadow}
    assert listed_routes == {"path", "fresh_shell_path", "python_subprocess_path"}
    assert all(entry["version_matches"] is not True for entry in shadow)
    assert all(
        entry["foreign"] is not False or entry["version_matches"] is not True for entry in shadow
    )
    assert [entry["route"] for entry in shadow] == [
        "path",
        "fresh_shell_path",
        "python_subprocess_path",
    ]  # deterministic spec order (codex REV-5 LOW)


def test_doctor_health_invalid_pypi_latest_is_unverifiable_not_ok() -> None:
    # codex HIGH 1: an invalid NON-NULL pypi_latest must land on unverifiable_version, never ok.
    assert (
        cli_main._doctor_installation_health(
            [],
            installed_version="1.110.13",
            installed_behind_pypi=None,
            pypi_unavailable=False,
            pypi_latest="not-a-version",
        )
        == "unverifiable_version"
    )


def test_doctor_route_version_matches_padded_equivalence() -> None:
    assert cli_main._doctor_route_version_matches("1.110.13", "tg 1.110.13") is True
    # PEP-440 padded: 1.0 == 1.0.0.
    assert cli_main._doctor_route_version_matches("1.0.0", "tg 1.0") is True
    # Semantic mismatch: old shadow vs installed.
    assert cli_main._doctor_route_version_matches("1.110.13", "tg 1.110.10") is False
    # Invalid route version -> None, never a confident False.
    assert cli_main._doctor_route_version_matches("1.110.13", "junk") is None
    # Absent route -> None.
    assert cli_main._doctor_route_version_matches("1.110.13", None) is None
    # Registry-looking / prerelease / local / epoch forms are unverifiable, not truncated
    # (plan REV 6: strict dotted-numeric, rejected = None):
    assert cli_main._doctor_version_tuple("1.110.13+dev") is None
    assert cli_main._doctor_version_tuple("1.110.13rc1") is None
    assert cli_main._doctor_version_tuple("1.110.13.dev0") is None
    assert cli_main._doctor_version_tuple("1.110.13+local") is None
    assert cli_main._doctor_version_tuple("1!2.0.0") is None
    assert cli_main._doctor_version_tuple("v1.110.13") is None


def test_doctor_shadow_launchers_absent_route_not_listed() -> None:
    # codex MEDIUM: a route with path=None (absent) must not be listed as unverifiable.
    routes = [
        {"route": "path", "path": None, "version": None, "foreign": False, "version_matches": None},
        {
            "route": "fresh_shell_path",
            "path": "/c/bin/old.tg",
            "version": "tg 1.110.10",
            "foreign": False,
            "version_matches": False,
        },
    ]
    shadow = cli_main._doctor_shadow_launchers(routes)
    assert [e["route"] for e in shadow] == ["fresh_shell_path"]


def test_doctor_shadow_launchers_order_is_spec() -> None:
    # codex LOW: deterministic order must be path, fresh_shell_path, python_subprocess_path.
    routes = [
        {
            "route": "python_subprocess_path",
            "path": "/c/bin/p3.tg",
            "version": "tg 0.32.0",
            "foreign": True,
            "version_matches": False,
        },
        {
            "route": "path",
            "path": "/c/bin/path.tg",
            "version": "tg 0.32.0",
            "foreign": True,
            "version_matches": False,
        },
        {
            "route": "fresh_shell_path",
            "path": "/c/bin/fresh.tg",
            "version": "tg 0.32.0",
            "foreign": True,
            "version_matches": False,
        },
    ]
    shadow = cli_main._doctor_shadow_launchers(routes)
    assert [e["route"] for e in shadow] == ["path", "fresh_shell_path", "python_subprocess_path"]


def test_doctor_behind_pypi_independent_of_route_versions() -> None:
    # codex REV-6: installed_behind_pypi depends ONLY on installed+pypi. A junk ROUTE version
    # (shadow presence) must not nullify the comparison.
    routes_with_junk = [
        {
            "route": "path",
            "path": "/c/bin/junk.tg",
            "version": "??",
            "foreign": False,
            "version_matches": None,
        },
    ]
    behind = cli_main._doctor_installed_behind_pypi("1.110.9", "1.110.13")
    assert behind is True  # route junk did not affect it
    assert (
        cli_main._doctor_installation_health(
            routes_with_junk,
            installed_version="1.110.9",
            installed_behind_pypi=behind,
            pypi_unavailable=False,
            pypi_latest="1.110.13",
        )
        == "unverifiable_version"
    )  # but health still surfaces the junk route
