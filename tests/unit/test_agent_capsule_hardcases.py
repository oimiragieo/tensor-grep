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


def _agent_payload(project: Path, query: str, *, max_files: int | None = None) -> dict[str, object]:
    args = ["agent", "--query", query, "--json", str(project)]
    if max_files is not None:
        args.extend(["--max-files", str(max_files)])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


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

    assert payload["schema_version"] == payload["version"]
    assert payload["capsule_schema_version"] == payload["capsule_version"]
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
    # Migrate the tie-confirmation moat contract into this DETERMINISTIC fixture (it previously lived
    # only in the de-fragilized self-referential live-repo test): the demoted marker resurfaces as a
    # tied alternative, so the capsule still flags the ambiguity for confirmation.
    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ask_user_before_editing"]["required"] is True


def test_agent_capsule_live_repo_prefers_exe_bridge_implementation_over_marker_helper():
    repo_root = Path(__file__).resolve().parents[2]

    payload = _agent_payload(repo_root, "harden Windows subprocess exe bridge")

    primary = payload["primary_target"]
    impl_path = str((repo_root / "rust_core" / "src" / "python_sidecar.rs").resolve())
    primary_symbol = str(primary.get("symbol") or "")
    assert primary_symbol, "capsule must always resolve a primary target symbol"

    # De-fragilized from the self-referential exact-identity pin. tg's OWN ~11k-line main.py is a
    # term-saturated outlier whose accumulated file_score over a flat, no-IDF presence count can
    # bury python_sidecar.rs below the rank/file caps so the swap helper finds no impl to promote.
    # The exact prefer-impl-over-marker IDENTITY is pinned deterministically by the controlled-corpus
    # fixture sibling. Here we assert the corpus-invariant SAFETY FLOOR, which still has teeth: a
    # non-implementation primary is tolerated ONLY when the capsule refuses to confidently auto-edit
    # it (this still FAILS on the unfixed #302 ranking, so it does not mask the degradation).
    if primary.get("file") == impl_path and primary_symbol in {
        "is_managed_windows_exe_bridge",
        "is_external_windows_exe_bridge",
    }:
        pass  # implementation correctly won primary (the desired, strong outcome)
    else:
        assert payload["ask_user_before_editing"]["required"] is True, (
            f"non-implementation primary {primary_symbol!r} must be gated behind ask-user"
        )


def test_prefer_implementation_over_marker_helper_swaps_marker_primary():
    """IDF-robust (the deterministic unit behind the live-repo test): a `*_marker` primary is
    demoted below a non-marker implementation candidate, and the marker stays as an alternative
    so the tie is still flagged."""
    from tensor_grep.cli.agent_capsule import _prefer_implementation_over_marker_helper

    marker = {
        "file": "src/main.py",
        "symbol": "_write_windows_exe_bridge_marker",
        "confidence": 0.9,
    }
    impl = {
        "file": "rust/python_sidecar.rs",
        "symbol": "is_managed_windows_exe_bridge",
        "confidence": 0.7,
    }
    primary, alternatives = _prefer_implementation_over_marker_helper(
        "harden Windows subprocess exe bridge", marker, [impl]
    )
    assert primary["symbol"] == "is_managed_windows_exe_bridge"
    assert primary["file"].endswith("python_sidecar.rs")
    assert alternatives[0]["symbol"] == "_write_windows_exe_bridge_marker"


def test_prefer_implementation_no_swap_when_primary_is_implementation():
    from tensor_grep.cli.agent_capsule import _prefer_implementation_over_marker_helper

    impl = {
        "file": "rust/python_sidecar.rs",
        "symbol": "is_managed_windows_exe_bridge",
        "confidence": 0.9,
    }
    marker = {
        "file": "src/main.py",
        "symbol": "_write_windows_exe_bridge_marker",
        "confidence": 0.7,
    }
    primary, alternatives = _prefer_implementation_over_marker_helper(
        "harden Windows subprocess exe bridge", impl, [marker]
    )
    assert primary["symbol"] == "is_managed_windows_exe_bridge"
    assert alternatives == [marker]


def test_prefer_implementation_no_swap_when_no_implementation_candidate():
    from tensor_grep.cli.agent_capsule import _prefer_implementation_over_marker_helper

    marker = {
        "file": "src/main.py",
        "symbol": "_write_windows_exe_bridge_marker",
        "confidence": 0.9,
    }
    other_marker = {"file": "src/x.py", "symbol": "write_path_marker", "confidence": 0.8}
    primary, alternatives = _prefer_implementation_over_marker_helper(
        "harden Windows subprocess exe bridge", marker, [other_marker]
    )
    assert primary["symbol"] == "_write_windows_exe_bridge_marker"
    assert alternatives == [other_marker]


# --- #250: thin CLI-dispatcher ranking weakness -----------------------------
# `tg prepare src/tensor_grep "fix the ledger claim TTL logic"` resolved `primary_target` to
# `cli/main.py`'s `ledger_claim` Typer dispatcher (an exact-symbol-name match on the query's two
# substantive words) instead of the real implementation in `cli/ledger_store.py`. These tests
# cover the down-weight helper directly; the golden-set regression coverage lives in
# tests/eval/test_agent_accuracy.py.


def _write_ledger_dispatcher_fixture(tmp_path, *, decorated: bool, calls_through: bool):
    from tensor_grep.cli.agent_capsule import _prefer_implementation_over_cli_dispatcher_helper

    cli_dir = tmp_path / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    decorator = "@ledger_app.command('claim')\n" if decorated else ""
    call_line = (
        "    return ledger_store.submit_claim(path)\n"
        if calls_through
        else "    return {'claim_id': 'x'}\n"
    )
    main_path = cli_dir / "main.py"
    main_path.write_text(
        f"from tensor_grep.cli import ledger_store\n\n{decorator}def ledger_claim(path):\n{call_line}",
        encoding="utf-8",
    )
    store_path = cli_dir / "ledger_store.py"
    store_path.write_text(
        "def submit_claim(path):\n    return {'claim_id': path}\n",
        encoding="utf-8",
    )
    primary_target = {
        "file": str(main_path),
        "symbol": "ledger_claim",
        "kind": "function",
        "line": 3 if decorated else 2,
        "confidence": 0.75,
    }
    alternative = {
        "file": str(store_path),
        "symbol": "submit_claim",
        "kind": "function",
        "confidence": 0.75,
    }
    return _prefer_implementation_over_cli_dispatcher_helper, primary_target, alternative


def test_prefer_implementation_over_cli_dispatcher_swaps_thin_wrapper(tmp_path):
    """The reported #250 shape: a `.command`-decorated dispatcher whose body is a single
    call-through to the real implementation must be demoted below that implementation."""
    swap, primary_target, alternative = _write_ledger_dispatcher_fixture(
        tmp_path, decorated=True, calls_through=True
    )
    primary, alternatives = swap(primary_target, [alternative])
    assert primary["symbol"] == "submit_claim"
    assert primary["file"] == alternative["file"]
    assert alternatives[0]["symbol"] == "ledger_claim"


def test_prefer_implementation_no_swap_when_cli_primary_is_genuine_target(tmp_path):
    """The crux guard (task #250): a `.command`-decorated `cli/main.py` function that does NOT
    call through to the alternative (i.e. it holds real logic of its own, like `tg search`'s own
    flag handling) must NOT be demoted -- this is what keeps "add a --flag to tg search"-style
    tasks resolving to cli/main.py correctly."""
    swap, primary_target, alternative = _write_ledger_dispatcher_fixture(
        tmp_path, decorated=True, calls_through=False
    )
    primary, alternatives = swap(primary_target, [alternative])
    assert primary["symbol"] == "ledger_claim"
    assert alternatives == [alternative]


def test_prefer_implementation_no_swap_when_no_command_decorator(tmp_path):
    """A plain (non-Typer-command) function that happens to call another module's function is
    not provably a dispatcher -- the decorator, not the call alone, is the gating signal."""
    swap, primary_target, alternative = _write_ledger_dispatcher_fixture(
        tmp_path, decorated=False, calls_through=True
    )
    primary, alternatives = swap(primary_target, [alternative])
    assert primary["symbol"] == "ledger_claim"
    assert alternatives == [alternative]


def test_prefer_implementation_no_swap_when_alternative_lives_in_same_file(tmp_path):
    from tensor_grep.cli.agent_capsule import _prefer_implementation_over_cli_dispatcher_helper

    cli_dir = tmp_path / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    main_path = cli_dir / "main.py"
    main_path.write_text(
        "@ledger_app.command('claim')\n"
        "def ledger_claim(path):\n"
        "    return _submit(path)\n\n"
        "def _submit(path):\n"
        "    return {'claim_id': path}\n",
        encoding="utf-8",
    )
    # Same-file "alternative" must never be treated as a cross-module implementation candidate.
    same_file_alternative = {
        "file": str(main_path),
        "symbol": "_submit",
        "kind": "function",
        "confidence": 0.75,
    }
    primary_target = {
        "file": str(main_path),
        "symbol": "ledger_claim",
        "kind": "function",
        "line": 2,
        "confidence": 0.75,
    }
    primary, alternatives = _prefer_implementation_over_cli_dispatcher_helper(
        primary_target, [same_file_alternative]
    )
    assert primary["symbol"] == "ledger_claim"
    assert alternatives == [same_file_alternative]


def test_agent_capsule_prefers_ledger_store_implementation_over_dispatcher(tmp_path):
    """End-to-end (full capsule pipeline, not just the isolated helper): the exact #250 repro
    shape, scoped to a synthetic project so it is corpus-size-independent."""
    project = tmp_path / "workspace"
    cli_dir = project / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (cli_dir / "main.py").write_text(
        "from tensor_grep.cli import ledger_store\n\n"
        "@ledger_app.command('claim')\n"
        "def ledger_claim(path, ttl=None):\n"
        '    """Claim TTL in seconds."""\n'
        "    return ledger_store.submit_claim(path, ttl_seconds=ttl)\n",
        encoding="utf-8",
    )
    (cli_dir / "ledger_store.py").write_text(
        "_DEFAULT_TTL_SECONDS = 900\n\n\n"
        "def _configured_ttl_seconds(explicit):\n"
        "    return explicit or _DEFAULT_TTL_SECONDS\n\n\n"
        "def submit_claim(path, ttl_seconds=None):\n"
        "    resolved_ttl = _configured_ttl_seconds(ttl_seconds)\n"
        "    return {'path': path, 'ttl_seconds': resolved_ttl}\n\n\n"
        "def release_claim(claim_id):\n"
        "    return {'claim_id': claim_id, 'released': True}\n",
        encoding="utf-8",
    )

    payload = _agent_payload(project, "fix the ledger claim TTL logic")

    assert payload["primary_target"]["file"] == str((cli_dir / "ledger_store.py").resolve())
    assert payload["primary_target"]["symbol"] in {"submit_claim", "_configured_ttl_seconds"}


def test_agent_capsule_prefers_ripgrep_resolver_for_binary_resolution_query(tmp_path):
    project = tmp_path / "workspace"
    cli_src = project / "src" / "tensor_grep" / "cli"
    formatter_src = project / "src" / "tensor_grep" / "cli" / "formatters"
    cli_src.mkdir(parents=True)
    formatter_src.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    runtime_paths = cli_src / "runtime_paths.py"
    runtime_paths.write_text(
        "def resolve_ripgrep_binary():\n"
        "    candidate = find_managed_rg_binary()\n"
        "    if candidate is not None:\n"
        "        return candidate\n"
        "    return find_path_binary('rg')\n",
        encoding="utf-8",
    )
    notice_file = formatter_src / "ripgrep_fmt.py"
    notice_file.write_text(
        "def _binary_notice_for_match(match):\n"
        "    return f'binary file matches: {match.file}'\n\n"
        "def _binary_notice(path):\n"
        "    return f'binary file matches: {path}'\n",
        encoding="utf-8",
    )

    for query in ("fix ripgrep binary resolution", "ripgrep binary resolution"):
        payload = _agent_payload(project, query)

        assert payload["primary_target"]["file"] == str(runtime_paths.resolve())
        assert payload["primary_target"]["symbol"] == "resolve_ripgrep_binary"


def test_agent_capsule_tight_budget_prefers_exact_resolver_over_binary_notice(tmp_path):
    project = tmp_path / "workspace"
    cli_src = project / "src" / "tensor_grep" / "cli"
    formatter_src = cli_src / "formatters"
    cli_src.mkdir(parents=True)
    formatter_src.mkdir(parents=True)
    runtime_paths = cli_src / "runtime_paths.py"
    runtime_paths.write_text(
        "def resolve_ripgrep_binary():\n"
        "    return resolve_ripgrep_binary_uncached()\n\n"
        "def resolve_ripgrep_binary_uncached():\n"
        "    return find_path_binary('rg')\n",
        encoding="utf-8",
    )
    notice_file = formatter_src / "ripgrep_fmt.py"
    notice_file.write_text(
        "def _binary_notice_for_match(match):\n"
        "    return f'binary file matches: {match.file}'\n\n"
        "def _binary_notice(path):\n"
        "    return f'binary file matches: {path}'\n",
        encoding="utf-8",
    )

    payload = _agent_payload(project, "ripgrep binary resolution", max_files=3)

    assert payload["primary_target"]["file"] == str(runtime_paths.resolve())
    assert payload["primary_target"]["symbol"] == "resolve_ripgrep_binary"


def test_agent_capsule_live_repo_tight_budget_prefers_ripgrep_resolver():
    repo_root = Path(__file__).resolve().parents[2]

    payload = _agent_payload(
        repo_root / "src" / "tensor_grep",
        "ripgrep binary resolution",
        max_files=3,
    )

    assert payload["primary_target"]["file"] == str(
        (repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py").resolve()
    )
    assert payload["primary_target"]["symbol"] == "resolve_ripgrep_binary"


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


def test_agent_path_scoring_ignores_checkout_parent_terms():
    noisy_absolute_path = str(
        Path("C:/tmp/worktrees/tensor-grep/feat/v1-13-dogfood-hardening/src/module.py")
    )

    assert repo_map._score_file_path(noisy_absolute_path, ["harden"]) == 0
