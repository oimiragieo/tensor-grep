import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app


def _write_polyglot_invoice_monorepo(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    py_src = project / "packages" / "billing" / "src" / "billing"
    py_tests = project / "packages" / "billing" / "tests"
    ts_src = project / "apps" / "web" / "src"
    js_src = project / "apps" / "admin" / "src"
    rust_src = project / "crates" / "billing" / "src"
    rust_tests = project / "crates" / "billing" / "tests"
    for directory in (py_src, py_tests, ts_src, js_src, rust_src, rust_tests):
        directory.mkdir(parents=True)

    python_path = py_src / "payments.py"
    python_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        "    return {'subtotal': subtotal, 'tax': tax, 'total': total}\n",
        encoding="utf-8",
    )
    (py_src / "__init__.py").write_text("", encoding="utf-8")
    python_test = py_tests / "test_payments.py"
    python_test.write_text(
        "from billing.payments import TAX_RATE, create_invoice\n\n"
        "def test_create_invoice_tax_calculation():\n"
        "    invoice = create_invoice(100)\n"
        "    assert invoice['tax'] == 100 * TAX_RATE\n",
        encoding="utf-8",
    )

    typescript_path = ts_src / "invoice.ts"
    typescript_path.write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  return subtotal + taxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )
    js_path = js_src / "invoice.js"
    js_path.write_text(
        "export function createInvoicePreview(subtotal) {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  return subtotal + taxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        json.dumps({
            "name": "polyglot-invoice",
            "devDependencies": {"vitest": "^1.0.0"},
        }),
        encoding="utf-8",
    )

    rust_path = rust_src / "lib.rs"
    rust_path.write_text(
        "pub fn create_invoice(subtotal: f64) -> f64 {\n"
        "    let tax_calculation = subtotal * 0.0825;\n"
        "    subtotal + tax_calculation\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "crates" / "billing" / "Cargo.toml").write_text(
        '[package]\nname = "billing"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (rust_tests / "invoice_tax.rs").write_text(
        "#[test]\nfn create_invoice_tax_calculation() { assert!(billing::create_invoice(100.0) > 100.0); }\n",
        encoding="utf-8",
    )

    for index in range(18):
        noise_dir = project / "packages" / f"pkg_{index}" / "src"
        noise_dir.mkdir(parents=True)
        (noise_dir / "invoice_notes.py").write_text(
            f"def invoice_note_{index}():\n    return 'not tax behavior'\n",
            encoding="utf-8",
        )

    generated_paths = [
        project / ".venv" / "Lib" / "site-packages" / "noise" / "payments.py",
        project / "node_modules" / "noise" / "invoice.ts",
        project / "dist" / "generated" / "invoice.js",
        project / "crates" / "billing" / "target" / "debug" / "build" / "generated.rs",
    ]
    for path in generated_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def create_invoice(subtotal):\n"
            "    tax = subtotal * 0.99\n"
            "    return {'tax': tax, 'total': subtotal + tax}\n",
            encoding="utf-8",
        )

    return {
        "project": project,
        "python": python_path,
        "python_test": python_test,
        "typescript": typescript_path,
        "javascript": js_path,
        "rust": rust_path,
    }


def _agent_payload(project: Path, query: str) -> dict[str, object]:
    result = CliRunner().invoke(app, ["agent", "--query", query, "--json", str(project)])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _referenced_files(payload: dict[str, object]) -> list[str]:
    files: list[str] = []
    files.extend(
        str(item.get("file"))
        for item in payload.get("snippets", [])
        if isinstance(item, dict) and item.get("file")
    )
    files.extend(
        str(item.get("file"))
        for item in payload.get("alternative_targets", [])
        if isinstance(item, dict) and item.get("file")
    )
    omissions = payload.get("omissions", {})
    if isinstance(omissions, dict):
        files.extend(str(item) for item in omissions.get("follow_up_reads", []) if item)
    return files


def test_agent_capsule_hardcase_prefers_python_source_over_polyglot_noise(tmp_path):
    paths = _write_polyglot_invoice_monorepo(tmp_path)

    payload = _agent_payload(paths["project"], "python invoice tax calculation")

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "python"
    assert any("pytest" in command for command in payload["validation_commands"])
    assert payload["ask_user_before_editing"]["required"] is False
    referenced = _referenced_files(payload)
    assert not any(
        marker in path
        for marker in ("node_modules", ".venv", "\\dist\\", "/dist/", "\\target\\", "/target/")
        for path in referenced
    )


def test_agent_capsule_hardcase_surfaces_cross_language_alternatives_without_promoting_generated(
    tmp_path,
):
    paths = _write_polyglot_invoice_monorepo(tmp_path)

    payload = _agent_payload(paths["project"], "change invoice tax calculation")

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    alternative_files = {item["file"] for item in payload["alternative_targets"]}
    assert str(paths["typescript"].resolve()) in alternative_files
    assert all("node_modules" not in path and ".venv" not in path for path in alternative_files)
    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] in {"tie_resolved", "none"}
    assert ambiguity["requires_confirmation"] is False


def test_agent_capsule_hardcase_rust_language_hint_selects_rust_target(tmp_path):
    paths = _write_polyglot_invoice_monorepo(tmp_path)

    payload = _agent_payload(paths["project"], "rust create_invoice tax calculation")

    assert payload["primary_target"]["file"] == str(paths["rust"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["rust"]
    assert payload["context_consistency"]["primary_target_language"] == "rust"


def test_agent_capsule_keeps_rust_validation_for_cli_parser_intent_with_python_tests(
    tmp_path,
):
    project = tmp_path / "workspace"
    rust_src = project / "rust_core" / "src"
    rust_src.mkdir(parents=True)
    tests_dir = project / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (project / "rust_core" / "Cargo.toml").write_text(
        '[package]\nname = "sample-rust-core"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    rust_file = rust_src / "main.rs"
    rust_file.write_text(
        "pub fn parse_native_cli_flags_passthru() -> bool {\n    true\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "test_cli_modes.py").write_text(
        "def test_cli_flags_passthru_parser_error():\n    assert True\n",
        encoding="utf-8",
    )

    payload = _agent_payload(
        project,
        "rust parse_native_cli_flags_passthru CLI parser passthru failure",
    )

    assert payload["primary_target"]["file"] == str(rust_file.resolve())
    assert payload["context_consistency"]["primary_target_language"] == "rust"
    assert payload["validation_commands"] == ["cargo test --manifest-path rust_core/Cargo.toml"]
    assert payload["context_consistency"]["validation_alignment"]["kept_count"] == 1


def test_agent_capsule_prefers_windows_exe_bridge_implementation_over_marker_helpers(
    tmp_path,
):
    project = tmp_path / "workspace"
    python_src = project / "src" / "tensor_grep" / "cli"
    rust_src = project / "rust_core" / "src"
    noise_src = project / "src" / "noise"
    python_src.mkdir(parents=True)
    rust_src.mkdir(parents=True)
    noise_src.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (project / "rust_core" / "Cargo.toml").write_text(
        '[package]\nname = "sample-rust-core"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    python_file = python_src / "main.py"
    python_file.write_text(
        "def _windows_exe_bridge_marker_path(root):\n"
        "    return root / 'tg.com'\n\n"
        "def _write_windows_exe_bridge_marker(root):\n"
        "    return _windows_exe_bridge_marker_path(root)\n\n"
        "def _windows_python_subprocess_resolution_blocker(path):\n"
        "    return path.name == 'tg.exe'\n",
        encoding="utf-8",
    )
    rust_file = rust_src / "python_sidecar.rs"
    rust_file.write_text(
        "pub fn is_external_windows_exe_bridge(path: &std::path::Path) -> bool {\n"
        '    let filename = path.file_name().and_then(|value| value.to_str()).unwrap_or("");\n'
        '    filename.eq_ignore_ascii_case("tg.exe") && !is_managed_windows_exe_bridge(path)\n'
        "}\n\n"
        "pub fn is_managed_windows_exe_bridge(path: &std::path::Path) -> bool {\n"
        '    let parent = path.parent().and_then(|value| value.file_name()).and_then(|value| value.to_str()).unwrap_or("");\n'
        '    path.file_name().and_then(|value| value.to_str()).unwrap_or("").eq_ignore_ascii_case("tg.exe")\n'
        '        && parent == "bin"\n'
        "}\n",
        encoding="utf-8",
    )
    (noise_src / "executor.py").write_text(
        "def execute_noise_bridge():\n    return 'not a windows exe bridge implementation'\n",
        encoding="utf-8",
    )

    payload = _agent_payload(project, "harden Windows subprocess exe bridge")

    assert payload["primary_target"]["file"] == str(rust_file.resolve())
    assert payload["primary_target"]["symbol"] == "is_managed_windows_exe_bridge"


def test_agent_capsule_live_repo_prefers_exe_bridge_implementation_over_marker_helper():
    repo_root = Path(__file__).resolve().parents[2]

    payload = _agent_payload(repo_root, "harden Windows subprocess exe bridge")

    assert payload["primary_target"]["file"] == str(
        (repo_root / "rust_core" / "src" / "python_sidecar.rs").resolve()
    )
    assert payload["primary_target"]["symbol"] in {
        "is_managed_windows_exe_bridge",
        "is_external_windows_exe_bridge",
    }
    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ask_user_before_editing"]["required"] is True


def test_agent_capsule_marker_query_keeps_exe_bridge_marker_primary():
    repo_root = Path(__file__).resolve().parents[2]

    payload = _agent_payload(repo_root, "harden Windows exe bridge marker")

    assert payload["primary_target"]["file"] == str(
        (repo_root / "src" / "tensor_grep" / "cli" / "main.py").resolve()
    )
    assert payload["primary_target"]["symbol"] == "_write_windows_exe_bridge_marker"


def test_agent_capsule_short_exe_term_does_not_match_execute_noise():
    assert repo_map._score_text_terms("execute_noise_bridge", ["exe"]) == 0
    assert repo_map._score_text_terms("tg.exe bridge", ["exe"]) == 1
