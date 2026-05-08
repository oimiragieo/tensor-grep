import importlib.util
import json
import subprocess
from pathlib import Path


def _load_script_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "agent_readiness.py"
    spec = importlib.util.spec_from_file_location("agent_readiness_script", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_readiness_plan_should_cover_agent_critical_surfaces() -> None:
    module = _load_script_module()

    checks = module.build_check_plan(
        repo_root=Path("C:/repo"),
        expected_version="1.8.22",
        include_shell_probes=True,
        include_wsl_probe=True,
    )

    names = {check.name for check in checks}
    assert "public-version-powershell" in names
    assert "public-version-cmd" in names
    assert "public-version-pwsh-noprofile" in names
    assert "public-version-git-bash" in names
    assert "public-version-wsl" in names
    assert "repo-doctor" in names
    assert "context-render-trust" in names
    assert "rg-parity-edges" in names
    assert "broad-generated-scan-guard" in names
    assert "ast-info-json" in names
    assert "ast-run-smoke" in names
    assert "mcp-context-render-smoke" in names
    assert "docs-claim-check" in names

    rg_check = next(check for check in checks if check.name == "rg-parity-edges")
    assert rg_check.timeout_s <= 180
    assert rg_check.command[:4] == ["uv", "run", "pytest", "tests/e2e/test_rg_parity_edges.py"]

    broad_scan_check = next(check for check in checks if check.name == "broad-generated-scan-guard")
    assert broad_scan_check.timeout_s <= 120
    assert broad_scan_check.command[:4] == ["uv", "run", "pytest", "tests/unit/test_cli_modes.py"]
    assert "broad_generated_root_scan" in broad_scan_check.command

    mcp_check = next(check for check in checks if check.name == "mcp-context-render-smoke")
    assert "test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target" in (
        mcp_check.command
    )


def test_agent_readiness_should_avoid_bare_tg_createprocess_on_windows(monkeypatch) -> None:
    module = _load_script_module()
    monkeypatch.setattr(module, "IS_WINDOWS", True)

    checks = module.build_check_plan(
        repo_root=Path("C:/repo"),
        expected_version="1.8.22",
        include_shell_probes=True,
        include_wsl_probe=False,
    )

    powershell_probe = next(check for check in checks if check.name == "public-version-powershell")
    assert powershell_probe.command[0].lower() == "powershell"
    assert "tg --version" in powershell_probe.command

    quoted_probe = next(
        check for check in checks if check.name == "public-windows-launcher-quoted-patterns"
    )
    assert quoted_probe.command == []
    assert quoted_probe.validator is module.validate_windows_launcher_quoted_patterns


def test_agent_readiness_windows_launcher_probe_rejects_split_quoted_patterns(
    monkeypatch, tmp_path
) -> None:
    module = _load_script_module()
    monkeypatch.setattr(module, "IS_WINDOWS", True)
    monkeypatch.setattr(module.shutil, "which", lambda name: "C:/Users/test/bin/tg.cmd")

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="artifacts/agent_readiness_launcher_argv.txt:agent launcher sentinel\n",
            stderr="rg: no-such-phrase: The system cannot find the file specified. (os error 2)\n",
        )

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    try:
        module.validate_windows_launcher_quoted_patterns("", tmp_path, "1.8.29")
    except module.ReadinessError as exc:
        assert "quoted multi-word no-match pattern" in str(exc)
    else:
        raise AssertionError("expected split quoted pattern probe to fail")


def test_agent_readiness_should_validate_context_render_trust_payload() -> None:
    module = _load_script_module()
    payload = {
        "edit_plan_seed": {"primary_file": "src/payments.py"},
        "navigation_pack": {"primary_target": {"file": "src/payments.py"}},
        "files": [{"path": "src/payments.py"}],
        "sources": [
            {
                "file": "src/payments.py",
                "name": "create_invoice",
                "rendered_source": (
                    "def create_invoice(subtotal):\n"
                    "    tax = subtotal * TAX_RATE\n"
                    "    return {'tax': tax}\n"
                ),
            }
        ],
        "rendered_context": "def create_invoice(subtotal):\n    tax = subtotal * TAX_RATE\n",
        "context_consistency": {"primary_file_represented": True},
    }

    module.validate_context_render_payload(json.dumps(payload), expected_fragment="TAX_RATE")


def test_agent_readiness_should_accept_current_doctor_backend_name() -> None:
    module = _load_script_module()
    payload = {
        "version": "1.8.22",
        "path_tg_first_version_matches": True,
        "search_acceleration_backend": "standalone-native-tg",
    }

    module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.8.22")


def test_agent_readiness_should_accept_native_and_python_version_prefixes() -> None:
    module = _load_script_module()

    module.validate_version_output("tensor-grep 1.8.26\n", Path("C:/repo"), "1.8.26")
    module.validate_version_output("tg 1.8.26\n", Path("C:/repo"), "1.8.26")


def test_agent_readiness_should_reject_signature_only_context_payload() -> None:
    module = _load_script_module()
    payload = {
        "edit_plan_seed": {"primary_file": "src/payments.py"},
        "navigation_pack": {"primary_target": {"file": "src/payments.py"}},
        "files": [{"path": "src/payments.py"}],
        "sources": [
            {
                "file": "src/payments.py",
                "name": "create_invoice",
                "rendered_source": "def create_invoice(subtotal):\n",
            }
        ],
        "rendered_context": "def create_invoice(subtotal):\n",
        "context_consistency": {"primary_file_represented": True},
    }

    try:
        module.validate_context_render_payload(json.dumps(payload), expected_fragment="TAX_RATE")
    except module.ReadinessError as exc:
        assert "missing expected context fragment" in str(exc)
    else:
        raise AssertionError("expected context payload validation to fail")


def test_agent_readiness_main_should_write_json_summary(monkeypatch, tmp_path) -> None:
    module = _load_script_module()

    monkeypatch.setattr(module, "read_project_version", lambda _root: "1.8.22")
    monkeypatch.setattr(
        module,
        "build_check_plan",
        lambda **_kwargs: [
            module.Check(
                name="docs-claim-check",
                command=[],
                description="Validate docs claims.",
                validator=lambda _stdout, _root, _expected: None,
            )
        ],
    )
    output_path = tmp_path / "agent-readiness.json"

    exit_code = module.main(["--output", str(output_path), "--no-shell-probes"])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["artifact"] == "agent_readiness_report"
    assert payload["expected_version"] == "1.8.22"
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["failed"] == 0
