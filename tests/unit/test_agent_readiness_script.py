import importlib.util
import io
import json
import subprocess
import sys
import time
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


def _write_docs_claim_fixture(repo_root: Path, version: str = "1.9.6") -> None:
    required_content = "\n".join([
        f"v{version}",
        "python scripts/agent_readiness.py",
        "context_consistency",
        "tg agent",
        "agent-capsule-hardcases",
        "validated compatibility set",
        "broad generated-root scan",
        "rg` remains",
        "ast-grep",
    ])
    for relative in (
        "AGENTS.md",
        "README.md",
        "SKILL.md",
        "docs/SESSION_HANDOFF.md",
        "docs/CONTINUATION_PLAN.md",
        "docs/CONTRACTS.md",
    ):
        path = repo_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(required_content, encoding="utf-8")

    gpu_content = "\n".join([
        f"post-`v{version}`",
        "1GB and 5GB correctness",
        "RTX 4070",
        "RTX 5070",
        "no crossover",
        "public managed",
        "not promotion-ready",
    ])
    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        path = repo_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = path.read_text(encoding="utf-8") if path.exists() else ""
        taxonomy = ""
        if relative in {"docs/benchmarks.md", "docs/gpu_crossover.md"}:
            taxonomy = "\n".join([
                "fair baseline is `rg -F -e ... -e ...`",
                "sidecar-routed rows are unsupported for native CUDA promotion",
            ])
        path.write_text("\n".join([prefix, gpu_content, taxonomy]), encoding="utf-8")


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
    if module.IS_WINDOWS:
        assert "public-doctor-cmd" in names
        assert "public-doctor-pwsh-noprofile" in names
        assert "public-version-python-subprocess" in names
    assert "public-search-advertised-flag-sweep" in names
    assert "repo-cli-build-warmup" in names
    assert "repo-doctor" in names
    assert "context-render-trust" in names
    assert "rg-parity-edges" in names
    assert "broad-generated-scan-guard" in names
    assert "ast-info-json" in names
    assert "ast-run-smoke" in names
    assert "mcp-context-render-smoke" in names
    assert "agent-capsule" in names
    assert "agent-capsule-mixed-language" in names
    assert "agent-capsule-hardcases" in names
    assert "docs-claim-check" in names

    rg_check = next(check for check in checks if check.name == "rg-parity-edges")
    assert rg_check.timeout_s <= 180
    assert rg_check.command[:5] == [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "tests/e2e/test_rg_parity_edges.py",
    ]

    broad_scan_check = next(check for check in checks if check.name == "broad-generated-scan-guard")
    assert broad_scan_check.timeout_s <= 120
    assert broad_scan_check.command[:5] == [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "tests/unit/test_cli_modes.py",
    ]
    assert "broad_generated_root_scan" in broad_scan_check.command

    warmup_check = next(check for check in checks if check.name == "repo-cli-build-warmup")
    assert warmup_check.command == ["uv", "run", "--no-sync", "tg", "--version"]
    assert warmup_check.timeout_s >= 180
    assert warmup_check.validator is module.validate_repo_cli_warmup_version_output

    mcp_check = next(check for check in checks if check.name == "mcp-context-render-smoke")
    assert "test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target" in (
        mcp_check.command
    )

    capsule_check = next(check for check in checks if check.name == "agent-capsule")
    assert capsule_check.timeout_s <= 120
    assert capsule_check.command[:6] == [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "tests/unit/test_cli_modes.py",
        "tests/unit/test_mcp_server.py",
    ]
    assert "agent_capsule" in capsule_check.command

    mixed_capsule_check = next(
        check for check in checks if check.name == "agent-capsule-mixed-language"
    )
    assert mixed_capsule_check.timeout_s <= 120
    assert mixed_capsule_check.command[:5] == [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "tests/unit/test_cli_modes.py",
    ]
    mixed_capsule_command = " ".join(mixed_capsule_check.command)
    assert "agent_capsule" in mixed_capsule_command
    assert "language" in mixed_capsule_command
    assert "validation" in mixed_capsule_command
    assert "invoice" in mixed_capsule_command
    assert "context_render_filters_pytest_only_validation_for_typescript_primary" in (
        mixed_capsule_command
    )
    assert "edit_plan_filters_pytest_only_validation_for_typescript_primary" in (
        mixed_capsule_command
    )

    hardcase_check = next(check for check in checks if check.name == "agent-capsule-hardcases")
    assert hardcase_check.timeout_s <= 120
    assert hardcase_check.command[:5] == [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "tests/unit/test_agent_capsule_hardcases.py",
    ]

    flag_sweep = next(
        check for check in checks if check.name == "public-search-advertised-flag-sweep"
    )
    assert flag_sweep.command == []
    assert flag_sweep.validator is module.validate_public_search_advertised_flag_sweep
    assert flag_sweep.timeout_s <= 60


def test_agent_readiness_docs_claims_cover_gpu_taxonomy(tmp_path) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path)

    module.validate_docs_claims("", tmp_path, "1.9.6")


def test_agent_readiness_docs_claims_reject_missing_gpu_taxonomy(tmp_path) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path)
    benchmarks_path = tmp_path / "docs" / "benchmarks.md"
    benchmarks_path.write_text(
        benchmarks_path.read_text(encoding="utf-8").replace(
            "fair baseline is `rg -F -e ... -e ...`",
            "",
        ),
        encoding="utf-8",
    )

    try:
        module.validate_docs_claims("", tmp_path, "1.9.6")
    except module.ReadinessError as exc:
        assert "fair baseline is `rg -F -e ... -e ...`" in str(exc)
    else:
        raise AssertionError("expected docs claim validation to fail")


def test_agent_readiness_docs_claims_reject_stale_current_release_prose(tmp_path) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path, version="1.9.12")
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8")
        + "\nThis checks the current `v1.9.10` shell/version resolution.\n",
        encoding="utf-8",
    )

    try:
        module.validate_docs_claims("", tmp_path, "1.9.12")
    except module.ReadinessError as exc:
        assert "stale current release prose" in str(exc)
        assert "v1.9.10" in str(exc)
    else:
        raise AssertionError("expected stale current release prose to fail")


def test_agent_readiness_docs_claims_reject_stale_latest_release_labels(tmp_path) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path, version="1.9.12")
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8")
        + "\nLatest tagged GitHub release: [`v1.9.10`](https://example.test/v1.9.10).\n"
        + "Latest complete PyPI release: [`v1.9.10`](https://example.test/v1.9.10).\n",
        encoding="utf-8",
    )

    try:
        module.validate_docs_claims("", tmp_path, "1.9.12")
    except module.ReadinessError as exc:
        message = str(exc)
        assert "stale latest tagged GitHub release" in message
        assert "stale latest complete PyPI release" in message
    else:
        raise AssertionError("expected stale latest release labels to fail")


def test_agent_readiness_docs_claims_allow_latest_complete_pypi_lag_when_current_tag_publication_failed(
    tmp_path,
) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path, version="1.11.0")
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8")
        + "\nLatest tagged GitHub release: [`v1.11.0`](https://example.test/v1.11.0).\n"
        + "Latest complete PyPI release: [`v1.10.10`](https://example.test/v1.10.10).\n"
        + "`v1.11.0` asset/PyPI publication did not complete; "
        + "`publish-success-gate` failed and PyPI latest remains `1.10.10`.\n",
        encoding="utf-8",
    )

    module.validate_docs_claims("", tmp_path, "1.11.0")


def test_agent_readiness_docs_claims_reject_stale_gpu_dogfood_label(tmp_path) -> None:
    module = _load_script_module()
    _write_docs_claim_fixture(tmp_path, version="1.9.12")
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8").replace(
            "post-`v1.9.12`",
            "post-`v1.9.10`",
            1,
        ),
        encoding="utf-8",
    )

    try:
        module.validate_docs_claims("", tmp_path, "1.9.12")
    except module.ReadinessError as exc:
        assert "post-`v1.9.12`" in str(exc)
    else:
        raise AssertionError("expected stale GPU dogfood label to fail")


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
    public_doctor = next(check for check in checks if check.name == "public-doctor-cmd")
    assert public_doctor.command == ["cmd", "/c", "tg doctor --json --no-lsp"]
    assert public_doctor.validator is module.validate_doctor_payload

    quoted_probe = next(
        check for check in checks if check.name == "public-windows-launcher-quoted-patterns"
    )
    assert quoted_probe.command == []
    assert quoted_probe.validator is module.validate_windows_launcher_quoted_patterns

    python_subprocess_probe = next(
        check for check in checks if check.name == "public-version-python-subprocess"
    )
    assert python_subprocess_probe.command[:2] == [sys.executable, "-c"]
    assert "subprocess.run(['tg', '--version']" in python_subprocess_probe.command[2]
    assert python_subprocess_probe.validator is module.validate_version_output


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
        "path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_version_matches": True,
        "python_subprocess_path_tg_first_launcher_kind": "managed-native",
        "python_subprocess_path_tg_first_version_matches": True,
        "search_acceleration_backend": "standalone-native-tg",
        "rust_binary_version_matches": True,
        "rust_binary_version_status": "matches",
    }

    module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.8.22")


def test_agent_readiness_should_reject_doctor_without_launcher_diagnostics() -> None:
    module = _load_script_module()
    payload = {
        "version": "1.8.22",
        "path_tg_first_version_matches": True,
        "search_acceleration_backend": "standalone-native-tg",
        "rust_binary_version_matches": True,
        "rust_binary_version_status": "matches",
    }

    try:
        module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.8.22")
    except module.ReadinessError as exc:
        assert "launcher route diagnostics" in str(exc)
    else:
        raise AssertionError("expected missing launcher diagnostics to fail")


def test_agent_readiness_should_reject_doctor_native_version_drift() -> None:
    module = _load_script_module()
    payload = {
        "version": "1.8.22",
        "path_tg_first_version_matches": True,
        "path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_version_matches": True,
        "python_subprocess_path_tg_first_launcher_kind": "managed-native",
        "python_subprocess_path_tg_first_version_matches": True,
        "search_acceleration_backend": "standalone-native-tg",
        "rust_binary_version_matches": False,
        "rust_binary_version_status": "stale",
    }

    try:
        module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.8.22")
    except module.ReadinessError as exc:
        assert "managed native-upgrade contract" in str(exc)
    else:
        raise AssertionError("expected native version drift to fail")


def test_agent_readiness_should_report_foreign_path_tg_remediation() -> None:
    module = _load_script_module()
    payload = {
        "version": "1.9.4",
        "path_tg_first_version_matches": False,
        "path_tg_first_launcher_kind": "foreign",
        "path_tg_foreign_warning": (
            "first PATH tg is not tensor-grep: C:/Python314/Scripts/tg.exe reports "
            "Together CLI (v2.12.0)"
        ),
        "path_tg_foreign_remediation": (
            "Move C:/Users/oimir/.tensor-grep/bin earlier in PATH than "
            "C:/Python314/Scripts or rename the foreign tg command outside tensor-grep."
        ),
        "fresh_shell_path_tg_first_launcher_kind": "foreign",
        "fresh_shell_path_tg_first_version_matches": False,
        "fresh_shell_path_tg_foreign_warning": (
            "first fresh-shell PATH tg is not tensor-grep: "
            "C:/Python314/Scripts/tg.exe reports Together CLI (v2.12.0)"
        ),
        "fresh_shell_path_tg_foreign_remediation": (
            "Move C:/Users/oimir/.tensor-grep/bin earlier in PATH than "
            "C:/Python314/Scripts or rename the foreign tg command outside tensor-grep."
        ),
        "search_acceleration_backend": "standalone-native-tg",
        "rust_binary_version_matches": True,
        "rust_binary_version_status": "matches",
    }

    try:
        module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.9.4")
    except module.ReadinessError as exc:
        message = str(exc)
        assert "not tensor-grep" in message
        assert "Together CLI" in message
        assert "Move C:/Users/oimir/.tensor-grep/bin earlier in PATH" in message
    else:
        raise AssertionError("expected foreign PATH tg to fail with remediation")


def test_agent_readiness_should_accept_stale_skipped_in_tree_native_binary() -> None:
    module = _load_script_module()
    payload = {
        "version": "1.9.0",
        "path_tg_first_version_matches": True,
        "path_tg_first_launcher_kind": "python-entrypoint",
        "fresh_shell_path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_version_matches": True,
        "python_subprocess_path_tg_first_launcher_kind": "managed-native",
        "python_subprocess_path_tg_first_version_matches": True,
        "search_acceleration_backend": "rust-core-extension",
        "rust_binary_version_matches": None,
        "rust_binary_version_status": "stale-skipped",
    }

    module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.9.0")


def test_agent_readiness_repo_doctor_should_allow_public_shell_version_lag_when_shell_probes_are_disabled() -> (
    None
):
    module = _load_script_module()
    payload = {
        "version": "1.11.0",
        "path_tg_first_version_matches": True,
        "path_tg_first_launcher_kind": "python-entrypoint",
        "fresh_shell_path_tg_first_launcher_kind": "managed-native",
        "fresh_shell_path_tg_first_version_matches": False,
        "fresh_shell_path_tg_first_is_foreign": False,
        "fresh_shell_path_tg_first_version": "tg 1.10.10",
        "python_subprocess_path_tg_first_launcher_kind": "python-entrypoint",
        "python_subprocess_path_tg_first_version_matches": True,
        "search_acceleration_backend": "standalone-native-tg",
        "rust_binary_version_matches": True,
        "rust_binary_version_status": "matches",
    }

    checks = module.build_check_plan(
        repo_root=Path("C:/repo"),
        expected_version="1.11.0",
        include_shell_probes=False,
        include_wsl_probe=False,
    )
    repo_doctor = next(check for check in checks if check.name == "repo-doctor")

    try:
        module.validate_doctor_payload(json.dumps(payload), Path("C:/repo"), "1.11.0")
    except module.ReadinessError as exc:
        assert "fresh-shell tg version does not match" in str(exc)
    else:
        raise AssertionError("expected public doctor validation to reject stale fresh-shell tg")

    assert repo_doctor.validator is not None
    repo_doctor.validator(json.dumps(payload), Path("C:/repo"), "1.11.0")


def test_agent_readiness_should_accept_native_and_python_version_prefixes() -> None:
    module = _load_script_module()

    module.validate_version_output("tensor-grep 1.8.26\n", Path("C:/repo"), "1.8.26")
    module.validate_version_output("tg 1.8.26\n", Path("C:/repo"), "1.8.26")


def test_agent_readiness_repo_cli_warmup_reports_stale_uv_entrypoint() -> None:
    module = _load_script_module()

    try:
        module.validate_repo_cli_warmup_version_output(
            "tensor-grep 1.12.32\n",
            Path("C:/repo"),
            "1.12.33",
        )
    except module.ReadinessError as exc:
        message = str(exc)
        assert "repo-local uv/tg entrypoint is stale" in message
        assert "expected one of" in message
        assert "uv run --refresh-package tensor-grep tg --version" in message
    else:
        raise AssertionError("expected stale repo cli warmup to fail")


def test_agent_readiness_public_search_flag_sweep_rejects_native_frontdoor_drift(
    monkeypatch, tmp_path
) -> None:
    module = _load_script_module()
    calls: list[list[str]] = []

    monkeypatch.setattr(
        module.shutil, "which", lambda command: command if command == "tg" else None
    )

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command == ["tg", "search", "--help"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="\n".join([
                    "Usage: tg search [OPTIONS]",
                    "  -H, --with-filename",
                    "  -I, --no-filename",
                    "  -q, --quiet",
                    "  -N, --no-line-number",
                    "      --stats",
                    "      --debug",
                    "      --trace",
                    "      --pcre2-unicode",
                    "      --no-pcre2-unicode",
                    "      --no-auto-hybrid-regex",
                    "      --no-text",
                    "      --no-binary",
                    "      --no-follow",
                    "      --no-glob-case-insensitive",
                    "      --no-ignore-file-case-insensitive",
                    "      --ignore",
                    "      --ignore-dot",
                    "      --ignore-exclude",
                    "      --ignore-files",
                    "      --ignore-global",
                    "      --ignore-messages",
                    "      --ignore-parent",
                    "      --ignore-vcs",
                    "      --messages",
                    "      --require-git",
                    "      --no-hidden",
                    "      --no-one-file-system",
                    "      --no-block-buffered",
                    "      --no-byte-offset",
                    "      --column",
                    "      --no-column",
                    "      --no-crlf",
                    "      --no-encoding",
                    "      --no-fixed-strings",
                    "      --no-invert-match",
                    "      --no-mmap",
                    "      --no-multiline",
                    "      --no-multiline-dotall",
                    "      --no-pcre2",
                    "      --no-pre",
                    "      --no-search-zip",
                    "      --no-context-separator",
                    "      --no-include-zero",
                    "      --no-line-buffered",
                    "      --no-max-columns-preview",
                    "      --no-trim",
                    "      --no-json",
                    "      --no-stats",
                    "      --engine <ENGINE>",
                    "  -s, --case-sensitive",
                    "  -x, --line-regexp",
                    "  -j, --threads <THREADS>",
                    "      --iglob <GLOB>",
                    "  -T, --type-not <TYPE>",
                    "  -u, --unrestricted",
                    "      --sort <SORTBY>",
                    "      --format <FORMAT>",
                    "  -n, --line-number",
                    "  -F, --fixed-strings",
                ]),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=2,
            stdout="",
            stderr="error: unexpected argument '-H' found\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    try:
        module.validate_public_search_advertised_flag_sweep("", tmp_path, "1.12.28")
    except module.ReadinessError as exc:
        assert "public search advertised flag sweep failed" in str(exc)
        assert "-H" in str(exc)
        assert "unexpected argument" in str(exc)
    else:
        raise AssertionError("expected public search flag sweep to fail")

    assert calls[0] == ["tg", "search", "--help"]


def test_agent_readiness_public_search_flag_sweep_includes_rg_inverse_overrides(
    tmp_path,
) -> None:
    module = _load_script_module()
    cases = module._public_search_flag_sweep_cases(tmp_path)
    commands = {" ".join(command) for _label, command in cases}
    batched_inverse_commands = [
        command for label, command in cases if label == "rg-inverse-config-overrides"
    ]
    column_toggle_commands = [
        command for label, command in cases if label == "column-no-column-last-wins"
    ]

    assert len(batched_inverse_commands) == 1
    batched_inverse_command = batched_inverse_commands[0]

    for flag in (
        "--no-auto-hybrid-regex",
        "--no-pcre2-unicode",
        "--no-text",
        "--no-binary",
        "--no-follow",
        "--no-glob-case-insensitive",
        "--no-ignore-file-case-insensitive",
        "--ignore-dot",
        "--ignore-exclude",
        "--ignore-files",
        "--ignore-global",
        "--ignore-messages",
        "--ignore-parent",
        "--ignore-vcs",
        "--no-one-file-system",
        "--no-block-buffered",
        "--no-byte-offset",
        "--no-column",
        "--no-crlf",
        "--no-encoding",
        "--no-fixed-strings",
        "--no-invert-match",
        "--no-mmap",
        "--no-multiline",
        "--no-multiline-dotall",
        "--no-pcre2",
        "--no-pre",
        "--no-search-zip",
        "--no-context-separator",
        "--no-include-zero",
        "--no-line-buffered",
        "--no-max-columns-preview",
        "--no-trim",
        "--no-json",
        "--no-stats",
    ):
        assert any(f" {flag} " in command for command in commands)
        assert flag in batched_inverse_command
    assert len(column_toggle_commands) == 1
    assert "--column" in column_toggle_commands[0]
    assert "--no-column" in column_toggle_commands[0]


def test_agent_readiness_public_search_flag_sweep_accepts_public_frontdoor(
    monkeypatch, tmp_path
) -> None:
    module = _load_script_module()
    seen_commands: list[list[str]] = []

    monkeypatch.setattr(
        module.shutil, "which", lambda command: command if command == "tg" else None
    )

    def fake_run(command, **_kwargs):
        seen_commands.append(list(command))
        if command == ["tg", "search", "--help"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="\n".join([
                    "Usage: tg search [OPTIONS]",
                    "  -H, --with-filename",
                    "  -I, --no-filename",
                    "  -q, --quiet",
                    "  -N, --no-line-number",
                    "      --stats",
                    "      --debug",
                    "      --trace",
                    "      --pcre2-unicode",
                    "      --no-pcre2-unicode",
                    "      --no-auto-hybrid-regex",
                    "      --no-text",
                    "      --no-binary",
                    "      --no-follow",
                    "      --no-glob-case-insensitive",
                    "      --no-ignore-file-case-insensitive",
                    "      --ignore",
                    "      --ignore-dot",
                    "      --ignore-exclude",
                    "      --ignore-files",
                    "      --ignore-global",
                    "      --ignore-messages",
                    "      --ignore-parent",
                    "      --ignore-vcs",
                    "      --messages",
                    "      --require-git",
                    "      --no-hidden",
                    "      --no-one-file-system",
                    "      --no-block-buffered",
                    "      --no-byte-offset",
                    "      --column",
                    "      --no-column",
                    "      --no-crlf",
                    "      --no-encoding",
                    "      --no-fixed-strings",
                    "      --no-invert-match",
                    "      --no-mmap",
                    "      --no-multiline",
                    "      --no-multiline-dotall",
                    "      --no-pcre2",
                    "      --no-pre",
                    "      --no-search-zip",
                    "      --no-context-separator",
                    "      --no-include-zero",
                    "      --no-line-buffered",
                    "      --no-max-columns-preview",
                    "      --no-trim",
                    "      --no-json",
                    "      --no-stats",
                    "      --engine <ENGINE>",
                    "  -s, --case-sensitive",
                    "  -x, --line-regexp",
                    "  -j, --threads <THREADS>",
                    "      --iglob <GLOB>",
                    "  -T, --type-not <TYPE>",
                    "  -u, --unrestricted",
                    "      --sort <SORTBY>",
                    "      --format <FORMAT>",
                    "  -n, --line-number",
                    "  -F, --fixed-strings",
                ]),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="accepted\n",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.validate_public_search_advertised_flag_sweep("", tmp_path, "1.12.28")

    flattened = [" ".join(command) for command in seen_commands]
    assert seen_commands[0] == ["tg", "search", "--help"]
    assert any("search -H" in command for command in flattened)
    assert any("search --stats" in command for command in flattened)
    assert any(" --no-stats " in command for command in flattened)
    assert any("search --pcre2-unicode" in command for command in flattened)
    assert any(" --no-auto-hybrid-regex " in command for command in flattened)
    assert any("search --no-hidden" in command for command in flattened)
    assert any(command.startswith("tg --sort path") for command in flattened)


def test_agent_readiness_public_search_flag_sweep_rejects_missing_help_advertisement(
    monkeypatch, tmp_path
) -> None:
    module = _load_script_module()

    monkeypatch.setattr(
        module.shutil, "which", lambda command: command if command == "tg" else None
    )

    def fake_run(command, **_kwargs):
        assert command == ["tg", "search", "--help"]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="Usage: tg search [OPTIONS]\n  -H, --with-filename\n",
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    try:
        module.validate_public_search_advertised_flag_sweep("", tmp_path, "1.12.28")
    except module.ReadinessError as exc:
        assert "search help missing advertised sweep flags" in str(exc)
        assert "--stats" in str(exc)
    else:
        raise AssertionError("expected missing help flag to fail")


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


def test_agent_readiness_json_auto_progress_keeps_stdout_json_and_captured_stderr_quiet(
    monkeypatch, tmp_path, capsys
) -> None:
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

    exit_code = module.main(["--root", str(tmp_path), "--json", "--no-shell-probes"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out)["summary"]["passed"] == 1
    assert captured.err == ""

    exit_code = module.main([
        "--root",
        str(tmp_path),
        "--json",
        "--progress",
        "never",
        "--no-shell-probes",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out)["summary"]["passed"] == 1
    assert captured.err == ""

    exit_code = module.main([
        "--root",
        str(tmp_path),
        "--json",
        "--progress",
        "auto",
        "--no-shell-probes",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out)["summary"]["passed"] == 1
    assert captured.err == ""


def test_agent_readiness_json_progress_always_uses_stderr_only(
    monkeypatch, tmp_path, capsys
) -> None:
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

    exit_code = module.main([
        "--root",
        str(tmp_path),
        "--json",
        "--progress",
        "always",
        "--no-shell-probes",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(captured.out)["summary"]["passed"] == 1
    assert "[progress]" in captured.err
    assert "[progress]" not in captured.out


def test_agent_readiness_run_check_caps_single_line_stdout_stderr(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    huge_stdout = "x" * (module.ARTIFACT_TAIL_LINE_CHAR_LIMIT + 25)
    huge_stderr = "y" * (module.ARTIFACT_TAIL_LINE_CHAR_LIMIT + 10)

    monkeypatch.setattr(module, "_command_available", lambda _command: True)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["demo"],
            returncode=0,
            stdout=huge_stdout,
            stderr=huge_stderr,
        ),
    )

    result = module.run_check(
        module.Check(
            name="giant-json-line",
            command=["demo"],
            description="demo",
        ),
        repo_root=tmp_path,
        expected_version="1.12.28",
    )

    assert result["status"] == "passed"
    assert len(result["stdout_tail"][0]) <= module.ARTIFACT_TAIL_LINE_CHAR_LIMIT + 80
    assert len(result["stderr_tail"][0]) <= module.ARTIFACT_TAIL_LINE_CHAR_LIMIT + 80
    assert "truncated" in result["stdout_tail"][0]
    assert "truncated" in result["stderr_tail"][0]


def test_progress_reporter_emits_phase_heartbeat_to_configured_stream() -> None:
    from tensor_grep.cli.progress import ProgressReporter

    stream = io.StringIO()
    reporter = ProgressReporter(
        mode="always",
        interval_s=0.001,
        json_output=True,
        stream=stream,
    )

    with reporter.phase("readiness"):
        deadline = time.monotonic() + 1.0
        while "readiness running" not in stream.getvalue() and time.monotonic() < deadline:
            time.sleep(0.005)

    lines = stream.getvalue().splitlines()
    assert lines[0] == "[progress] readiness start"
    assert any(line.startswith("[progress] readiness running ") for line in lines)
    assert lines[-1].startswith("[progress] readiness done ")


def test_progress_reporter_auto_emits_in_ci_without_json(monkeypatch) -> None:
    from tensor_grep.cli.progress import ProgressReporter

    stream = io.StringIO()
    monkeypatch.setenv("CI", "true")
    reporter = ProgressReporter(
        mode="auto",
        interval_s=30.0,
        json_output=False,
        stream=stream,
    )

    with reporter.phase("readiness"):
        pass

    assert stream.getvalue().splitlines() == [
        "[progress] readiness start",
        "[progress] readiness done 0s",
    ]
