import hashlib
import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tests.unit.test_cli_modes import (
    _canonical_manifest_bytes,
    _FakeAstPipeline,
    _FakeAstScanner,
    _write_audit_manifest,
    _write_scan_results,
)


def _assert_audit_manifest_envelope(payload: dict[str, object], *, routing_reason: str) -> None:
    assert payload["version"] == 1
    assert payload["routing_backend"] == "AuditManifest"
    assert payload["routing_reason"] == routing_reason
    assert payload["sidecar_used"] is False


def _prepare_review_bundle_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    from tensor_grep.cli.checkpoint_store import create_checkpoint

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
    _write_scan_results(scan_path)
    checkpoint = create_checkpoint(str(project))
    return project, previous_path, current_path, checkpoint.checkpoint_id


def test_audit_history_json_uses_standard_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    first_payload = _write_audit_manifest(audit_dir / "first.json")
    second_payload = _write_audit_manifest(
        audit_dir / "second.json",
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
    )

    result = runner.invoke(app, ["audit-history", str(project), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-history")
    assert [entry["manifest_sha256"] for entry in payload["history"]] == [
        second_payload["manifest_sha256"],
        first_payload["manifest_sha256"],
    ]


def test_audit_diff_json_uses_standard_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_payload = _write_audit_manifest(right_path, previous_manifest_sha256="f" * 64)
    parsed_right = json.loads(right_path.read_text(encoding="utf-8"))
    parsed_right["reviewer"] = "alice"
    parsed_right["files"][0]["after_sha256"] = "c" * 64
    parsed_right["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(parsed_right)
    ).hexdigest()
    right_path.write_text(json.dumps(parsed_right, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-diff")
    assert payload["added"] == {"reviewer": "alice"}
    assert payload["removed"] == {}
    assert payload["changed"]["previous_manifest_sha256"] == {
        "old": None,
        "new": "f" * 64,
    }
    assert right_payload["manifest_sha256"] != parsed_right["manifest_sha256"]


def test_review_bundle_create_json_uses_standard_envelope(tmp_path: Path) -> None:
    runner = CliRunner()
    _, previous_path, current_path, checkpoint_id = _prepare_review_bundle_fixture(tmp_path)
    bundle_path = tmp_path / "review-bundle.json"

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(current_path),
            "--checkpoint-id",
            checkpoint_id,
            "--previous-manifest",
            str(previous_path),
            "--output",
            str(bundle_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    _assert_audit_manifest_envelope(payload, routing_reason="review-bundle-create")
    assert payload["bundle_sha256"]
    assert json.loads(bundle_path.read_text(encoding="utf-8")) == payload


def test_review_bundle_verify_json_uses_standard_envelope(tmp_path: Path) -> None:
    from tensor_grep.cli import audit_manifest

    runner = CliRunner()
    _, _, current_path, _ = _prepare_review_bundle_fixture(tmp_path)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(current_path, output_path=bundle_path)

    result = runner.invoke(app, ["review-bundle", "verify", str(bundle_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    _assert_audit_manifest_envelope(payload, routing_reason="review-bundle-verify")
    assert payload["valid"] is True
    assert payload["bundle_integrity"]["valid"] is True


def test_mcp_tool_listing_includes_new_trust_tools() -> None:
    from tensor_grep.cli import mcp_server

    tools = mcp_server.mcp._tool_manager.list_tools()
    tool_names = {tool.name for tool in tools}

    assert {
        "tg_audit_history",
        "tg_audit_diff",
        "tg_review_bundle_create",
        "tg_review_bundle_verify",
    } <= tool_names


def test_tg_help_lists_new_trust_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "audit-history" in result.stdout
    assert "audit-diff" in result.stdout
    assert "review-bundle" in result.stdout


def test_main_entry_does_not_rewrite_new_trust_commands(monkeypatch) -> None:
    from tensor_grep.cli import main as cli_main

    observed_argv: dict[str, list[str]] = {}

    def _fake_app() -> None:
        observed_argv["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)

    for argv in (
        ["tg", "audit-history", "."],
        ["tg", "audit-diff", "left.json", "right.json"],
        ["tg", "audit-verify", "manifest.json"],
        ["tg", "review-bundle", "verify", "bundle.json"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        cli_main.main_entry()
        assert observed_argv["argv"] == argv


def test_scan_builtin_ruleset_json_without_new_flags_keeps_existing_contract(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {
        "version",
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "backends",
        "config_path",
        "ruleset",
        "language",
        "path",
        "rule_count",
        "matched_rules",
        "total_matches",
        "findings",
    }
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "crypto-safe"
    assert payload["findings"][0]["rule_id"] == "python-hashlib-md5"
