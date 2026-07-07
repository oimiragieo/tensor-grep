import json
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


@pytest.fixture(autouse=True)
def _clear_validation_caches() -> None:
    for name in dir(repo_map):
        if "validation" not in name and "test_function" not in name:
            continue
        candidate = getattr(repo_map, name)
        cache_clear = getattr(candidate, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()
    yield
    for name in dir(repo_map):
        if "validation" not in name and "test_function" not in name:
            continue
        candidate = getattr(repo_map, name)
        cache_clear = getattr(candidate, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def _write_package_json(
    project: Path,
    *,
    dependencies: dict[str, str] | None = None,
    dev_dependencies: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    package_manager: str | None = None,
    jest: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {}
    if package_manager:
        payload["packageManager"] = package_manager
    if scripts:
        payload["scripts"] = scripts
    if dependencies:
        payload["dependencies"] = dependencies
    if dev_dependencies:
        payload["devDependencies"] = dev_dependencies
    if jest is not None:
        payload["jest"] = jest
    (project / "package.json").write_text(json.dumps(payload), encoding="utf-8")


def _validation_commands(
    project: Path,
    tests: list[Path],
    *,
    primary_test: Path | None = None,
    primary_symbol_name: str | None = None,
    query: str | None = None,
) -> list[str]:
    primary_symbol = {"name": primary_symbol_name} if primary_symbol_name else None
    return repo_map._validation_commands_for_tests(
        [str(current.resolve()) for current in tests],
        repo_root=project,
        primary_test=str(primary_test.resolve()) if primary_test is not None else None,
        primary_symbol=primary_symbol,
        query=query,
    )


def test_validation_commands_without_precomputed_paths_uses_cold_file_walk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[Path, int | None]] = []

    def fake_iter_repo_files(
        root: Path,
        *,
        max_files: int | None = None,
        _profiling_collector=None,
    ) -> list[Path]:
        calls.append((Path(root).resolve(), max_files))
        return [Path(root) / "tests" / "test_payments.py"]

    monkeypatch.setattr(repo_map, "_iter_repo_files", fake_iter_repo_files)

    commands = repo_map._validation_commands_for_tests(
        [],
        repo_root=project,
        primary_symbol={
            "file": str(project / "src" / "payments.py"),
            "name": "create_invoice",
        },
        precomputed_file_paths=None,
    )

    assert commands == ["uv run pytest -q"]
    assert calls
    assert all(root == project.resolve() for root, _max_files in calls)
    assert any(max_files == repo_map._VALIDATION_RUNNER_SCAN_LIMIT for _root, max_files in calls)


@pytest.mark.parametrize(
    ("test_source", "expected_filter"),
    [
        (
            "def test_create_invoice():\n    assert True\n",
            "test_create_invoice",
        ),
        (
            "def test_create_invoice_smoke():\n    assert True\n",
            "test_create_invoice_smoke",
        ),
        (
            "def test_smoke():\n    assert True\n",
            "test_smoke",
        ),
    ],
)
def test_python_target_resolution_patterns(
    tmp_path: Path,
    test_source: str,
    expected_filter: str,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n" + test_source,
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="create_invoice",
    )

    assert commands == [
        f"uv run pytest tests/test_payments.py -k {expected_filter} -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]


def test_python_primary_test_without_match_skips_k_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "def test_shipping_total():\n    assert True\n\ndef test_tax_total():\n    assert True\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="create_invoice",
    )

    assert commands == [
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]


@pytest.mark.parametrize(
    "relative_test_path",
    [
        "tests/test_payments.py",
        "packages/payments/tests/test_payments.py",
    ],
)
def test_python_commands_use_relative_paths(
    tmp_path: Path,
    relative_test_path: str,
) -> None:
    project = tmp_path / "project"
    test_path = project / relative_test_path
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_create_invoice():\n    assert True\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="create_invoice",
    )

    assert (
        commands[0]
        == f"uv run pytest {relative_test_path.replace(chr(92), '/')} -k test_create_invoice -q"
    )
    assert not any(str(project.resolve()) in command for command in commands)


def test_node_test_script_prefers_targeted_file_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests" / "scripts"
    tests_dir.mkdir(parents=True)
    _write_package_json(
        project,
        package_manager="pnpm@10.0.0",
        scripts={"test": "node --test tests/**/*.test.cjs"},
    )
    test_path = tests_dir / "run-cursor-worker.test.cjs"
    test_path.write_text(
        "const test = require('node:test');\ntest('runCursorWorker invokes cursor', () => {});\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="runCursorWorker",
        query="run cursor worker",
    )

    assert commands[0] == "node --test tests/scripts/run-cursor-worker.test.cjs"
    assert commands[-1] == "pnpm test"


def test_node_test_file_prefers_targeted_command_with_generic_package_script(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests" / "scripts"
    tests_dir.mkdir(parents=True)
    _write_package_json(
        project,
        package_manager="pnpm@10.0.0",
        scripts={"test": "pnpm run test:unit"},
    )
    test_path = tests_dir / "run-cursor-worker.test.cjs"
    test_path.write_text(
        "const test = require('node:test');\ntest('runCursorWorker invokes cursor', () => {});\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="runCursorWorker",
        query="run cursor worker",
    )

    assert commands[0] == "node --test tests/scripts/run-cursor-worker.test.cjs"
    assert commands[-1] == "pnpm test"


def test_node_test_file_probe_only_reads_primary_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests" / "scripts"
    tests_dir.mkdir(parents=True)
    _write_package_json(
        project,
        package_manager="pnpm@10.0.0",
        scripts={"test": "pnpm run test:unit"},
    )
    primary_path = tests_dir / "run-cursor-worker.test.cjs"
    related_path = tests_dir / "unrelated.test.cjs"
    primary_path.write_text("test('primary', () => {});\n", encoding="utf-8")
    related_path.write_text("test('related', () => {});\n", encoding="utf-8")
    probed_paths: list[Path] = []

    def fake_node_test_probe(test_path: str) -> bool:
        probed_paths.append(Path(test_path))
        return Path(test_path) == primary_path.resolve()

    monkeypatch.setattr(repo_map, "_javascript_test_file_uses_node_test", fake_node_test_probe)

    commands = _validation_commands(
        project,
        [primary_path, related_path],
        primary_test=primary_path,
        primary_symbol_name="runCursorWorker",
    )

    assert probed_paths == [primary_path.resolve()]
    assert commands[0] == "node --test tests/scripts/run-cursor-worker.test.cjs"
    assert "node --test tests/scripts/unrelated.test.cjs" not in commands


@pytest.mark.parametrize(
    ("dependencies", "expected_specific_command", "expected_file_command", "expected_fallback"),
    [
        (
            {"jest": "^29.0.0"},
            "npx jest tests/widget.test.js --testNamePattern widget",
            "npx jest tests/widget.test.js",
            "npx jest",
        ),
        (
            {"vitest": "^2.0.0"},
            "npx vitest run tests/widget.test.js -t widget",
            "npx vitest run tests/widget.test.js",
            "npx vitest run",
        ),
        (
            {"mocha": "^10.0.0"},
            "npx mocha tests/widget.test.js --grep widget",
            "npx mocha tests/widget.test.js",
            "npx mocha",
        ),
    ],
)
def test_javascript_runner_detection(
    tmp_path: Path,
    dependencies: dict[str, str],
    expected_specific_command: str,
    expected_file_command: str,
    expected_fallback: str,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    _write_package_json(project, dev_dependencies=dependencies)
    test_path = tests_dir / "widget.test.js"
    test_path.write_text("test('widget', () => expect(1).toBe(1));\n", encoding="utf-8")

    commands = _validation_commands(project, [test_path], primary_test=test_path, query="widget")

    assert commands == [expected_specific_command, expected_file_command, expected_fallback]


@pytest.mark.parametrize(
    (
        "dev_dependencies",
        "jest_config",
        "expected_specific_command",
        "expected_file_command",
        "expected_fallback",
    ),
    [
        (
            {"vitest": "^2.0.0"},
            None,
            "npx vitest run tests/widget.spec.ts -t widget",
            "npx vitest run tests/widget.spec.ts",
            "npx vitest run",
        ),
        (
            {"jest": "^29.0.0", "ts-jest": "^29.0.0"},
            None,
            "npx jest tests/widget.spec.ts --testNamePattern widget",
            "npx jest tests/widget.spec.ts",
            "npx jest",
        ),
        (
            {"jest": "^29.0.0"},
            {"preset": "ts-jest"},
            "npx jest tests/widget.spec.ts --testNamePattern widget",
            "npx jest tests/widget.spec.ts",
            "npx jest",
        ),
    ],
)
def test_typescript_runner_detection(
    tmp_path: Path,
    dev_dependencies: dict[str, str],
    jest_config: dict[str, object] | None,
    expected_specific_command: str,
    expected_file_command: str,
    expected_fallback: str,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    _write_package_json(project, dev_dependencies=dev_dependencies, jest=jest_config)
    test_path = tests_dir / "widget.spec.ts"
    test_path.write_text("test('widget', () => expect(1).toBe(1));\n", encoding="utf-8")

    commands = _validation_commands(project, [test_path], primary_test=test_path, query="widget")

    assert commands == [expected_specific_command, expected_file_command, expected_fallback]


def test_typescript_plain_jest_without_ts_jest_skips_file_target(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    _write_package_json(project, dev_dependencies={"jest": "^29.0.0"})
    test_path = tests_dir / "widget.spec.ts"
    test_path.write_text("test('widget', () => expect(1).toBe(1));\n", encoding="utf-8")

    commands = _validation_commands(project, [test_path], primary_test=test_path, query="widget")

    assert commands == ["npx jest"]


@pytest.mark.parametrize(
    ("test_source", "expected_first_command"),
    [
        (
            "#[test]\nfn invoice_smoke() {\n    assert_eq!(1, 1);\n}\n",
            "cargo test invoice_smoke",
        ),
        (
            "#[test]\nfn smoke_test() {\n    assert_eq!(1, 1);\n}\n",
            "cargo test smoke_test",
        ),
    ],
)
def test_rust_target_resolution_patterns(
    tmp_path: Path,
    test_source: str,
    expected_first_command: str,
) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (project / "Cargo.toml").write_text(
        '[package]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    test_path = tests_dir / "integration_checks.rs"
    test_path.write_text(test_source, encoding="utf-8")

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="issue_invoice",
        query="invoice smoke",
    )

    assert commands == [
        expected_first_command,
        "cargo test --test integration_checks",
        "cargo test",
    ]


@pytest.mark.parametrize(
    ("setup_kind", "tests", "expected"),
    [
        ("python-source-only", [], []),
        ("python-project-marker", [], ["uv run pytest -q"]),
        ("bare-tests-dir", [], []),
        ("rust", [], ["cargo test"]),
        ("jest-unknown", ["notes/validation.txt"], ["npx jest"]),
        ("default-unknown", ["notes/validation.txt"], []),
    ],
)
def test_repo_wide_fallback_detection(
    tmp_path: Path,
    setup_kind: str,
    tests: list[str],
    expected: list[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    if setup_kind in {"python-source-only", "python-project-marker"}:
        src_dir = project / "src"
        src_dir.mkdir()
        (src_dir / "payments.py").write_text(
            "def create_invoice():\n    return 1\n", encoding="utf-8"
        )
        if setup_kind == "python-project-marker":
            (project / "pyproject.toml").write_text(
                '[project]\nname = "sample"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
    elif setup_kind == "bare-tests-dir":
        (project / "tests").mkdir()
    elif setup_kind == "rust":
        (project / "Cargo.toml").write_text(
            '[package]\nname = "sample"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
    elif setup_kind == "jest-unknown":
        notes_dir = project / "notes"
        notes_dir.mkdir()
        _write_package_json(project, dev_dependencies={"jest": "^29.0.0"})
        (notes_dir / "validation.txt").write_text("validate me\n", encoding="utf-8")
    else:
        notes_dir = project / "notes"
        notes_dir.mkdir()
        (notes_dir / "validation.txt").write_text("validate me\n", encoding="utf-8")

    command_tests = [project / current for current in tests]
    commands = _validation_commands(project, command_tests)

    assert commands == expected


def test_javascript_repo_fallback_prefers_package_test_script(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    _write_package_json(
        project,
        dev_dependencies={"jest": "^29.0.0"},
        scripts={"test": "jest --runInBand"},
        package_manager="pnpm@10.0.0",
    )
    (src_dir / "worker.cjs").write_text(
        "function runCursorWorker() {\n    return true;\n}\n",
        encoding="utf-8",
    )

    commands = _validation_commands(project, [])

    assert commands == ["pnpm test"]


def test_multi_language_repo_includes_commands_for_all_detected_languages(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    _write_package_json(project, dev_dependencies={"vitest": "^2.0.0"})
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    python_test = tests_dir / "test_payments.py"
    python_test.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )
    ts_test = tests_dir / "payments.spec.ts"
    ts_test.write_text(
        'import { createInvoice } from "../src/payments";\n'
        "test('invoice', () => expect(createInvoice(1)).toBe(1));\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [python_test, ts_test],
        primary_test=python_test,
        primary_symbol_name="create_invoice",
    )

    assert commands == [
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "npx vitest run tests/payments.spec.ts",
        "uv run pytest -q",
        "npx vitest run",
    ]


def test_rust_primary_keeps_manifest_validation_when_python_cli_tests_match(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
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
    primary_file = rust_src / "main.rs"
    primary_file.write_text(
        "pub fn parse_native_search_flags_passthru() -> bool {\n    true\n}\n",
        encoding="utf-8",
    )
    python_test = tests_dir / "test_cli_modes.py"
    python_test.write_text(
        "def test_cli_parser_rejects_passthru_flag():\n    assert True\n",
        encoding="utf-8",
    )

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(python_test.resolve())],
        repo_root=project,
        primary_file=str(primary_file.resolve()),
        query="rust native CLI parser passthru flag failure",
    )

    assert [step["command"] for step in plan] == ["cargo test --manifest-path rust_core/Cargo.toml"]
    assert alignment["primary_target_language"] == "rust"
    assert alignment["kept_count"] == 1
    assert alignment["filtered_count"] >= 1
    assert any("filtered pytest validation for rust" in issue for issue in alignment["issues"])


def test_rust_primary_replaces_heuristic_repo_fallback_with_nested_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    rust_src = project / "rust_core" / "src"
    rust_src.mkdir(parents=True)
    (project / "rust_core" / "Cargo.toml").write_text(
        '[package]\nname = "sample-rust-core"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = rust_src / "main.rs"
    primary_file.write_text(
        "pub fn parse_native_search_flags_passthru() -> bool {\n    true\n}\n",
        encoding="utf-8",
    )

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [],
        repo_root=project,
        primary_file=str(primary_file.resolve()),
        query="rust native CLI parser passthru flag failure",
    )

    assert [step["command"] for step in plan] == ["cargo test --manifest-path rust_core/Cargo.toml"]
    assert alignment["status"] == "aligned"
    assert alignment["kept_count"] == 1
    assert alignment["filtered_count"] == 0


def test_python_primary_uses_detected_repo_pytest_fallback_with_rust_present(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    python_src = project / "src"
    rust_src = project / "rust_core" / "src"
    tests_dir = project / "tests"
    python_src.mkdir(parents=True)
    rust_src.mkdir(parents=True)
    tests_dir.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (project / "rust_core" / "Cargo.toml").write_text(
        '[package]\nname = "sample-rust-core"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = python_src / "payments.py"
    primary_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (rust_src / "main.rs").write_text("pub fn native() -> bool { true }\n", encoding="utf-8")

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [],
        repo_root=project,
        primary_file=str(primary_file.resolve()),
        query="python cli source target",
    )

    assert plan == [
        {
            "command": "uv run pytest -q",
            "scope": "repo",
            "runner": "pytest",
            "confidence": 0.55,
            "detection": "detected",
        }
    ]
    assert alignment["status"] == "aligned"
    assert alignment["primary_target_language"] == "python"
    assert alignment["kept_count"] == 1
    assert alignment["filtered_count"] == 0


def test_python_primary_without_project_or_test_evidence_has_no_validation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_file = src_dir / "payments.py"
    primary_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [],
        repo_root=project,
        primary_file=str(primary_file.resolve()),
        query="python source target",
    )

    assert plan == []
    assert alignment["status"] == "no-validation"
    assert alignment["primary_target_language"] == "python"
    assert alignment["kept_count"] == 0
    assert alignment["filtered_count"] == 0


def test_rust_source_without_manifest_has_no_repo_fallback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    rust_src = project / "src"
    rust_src.mkdir(parents=True)
    primary_file = rust_src / "lib.rs"
    primary_file.write_text("pub fn parse_flags() -> bool { true }\n", encoding="utf-8")

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [],
        repo_root=project,
        primary_file=str(primary_file.resolve()),
        query="rust parser source target",
    )

    assert plan == []
    assert alignment["status"] == "no-validation"
    assert alignment["primary_target_language"] == "rust"
    assert alignment["kept_count"] == 0
    assert alignment["filtered_count"] == 0


def test_detect_validation_runners_is_cached_per_repo_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_package_json(project, dev_dependencies={"jest": "^29.0.0"})

    first = repo_map._detect_validation_runners(str(project.resolve()))

    _write_package_json(project, dev_dependencies={"vitest": "^2.0.0"})
    second = repo_map._detect_validation_runners(str(project.resolve()))

    assert second == first

    repo_map._detect_validation_runners.cache_clear()
    refreshed = repo_map._detect_validation_runners(str(project.resolve()))

    assert "vitest" in refreshed.js_runners
    assert "jest" not in refreshed.js_runners


def test_build_context_render_uses_relative_python_validation_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("create invoice", project)

    assert payload["edit_plan_seed"]["validation_commands"] == [
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]


def test_build_context_render_discovers_scoped_node_test_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts_dir = project / "scripts" / "agents"
    tests_dir = project / "tests" / "scripts"
    scripts_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    _write_package_json(
        project,
        package_manager="pnpm@10.0.0",
        scripts={"test": "pnpm run test:unit"},
    )
    source_path = scripts_dir / "run-cursor-worker.cjs"
    source_path.write_text(
        "function runCursorWorker() {\n  return true;\n}\nmodule.exports = { runCursorWorker };\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "run-cursor-worker.test.cjs"
    test_path.write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "const { runCursorWorker } = require('../../scripts/agents/run-cursor-worker.cjs');\n"
        "test('runCursorWorker invokes cursor', () => {\n"
        "  assert.equal(runCursorWorker(), true);\n"
        "});\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("run cursor worker", scripts_dir)

    assert payload["edit_plan_seed"]["primary_file"] == str(source_path.resolve())
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["edit_plan_seed"]["validation_commands"][0] == (
        "node --test tests/scripts/run-cursor-worker.test.cjs"
    )
    assert payload["edit_plan_seed"]["validation_commands"][-1] == "pnpm test"


def test_build_symbol_blast_radius_render_uses_rust_validation_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (project / "Cargo.toml").write_text(
        '[package]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (src_dir / "billing.rs").write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "integration_checks.rs").write_text(
        "use crate::billing::issue_invoice;\n\n"
        "#[test]\n"
        "fn invoice_smoke() {\n"
        "    assert_eq!(issue_invoice(), 1);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius_render("issue_invoice", project)

    assert payload["edit_plan_seed"]["validation_commands"] == [
        "cargo test invoice_smoke",
        "cargo test --test integration_checks",
        "cargo test",
    ]


def test_rust_framework_candidates_include_plain_test_functions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    test_path = project / "tests" / "testsuite" / "parsed.rs"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "#[test]\n"
        "fn is_escape() {\n"
        "    assert!(true);\n"
        "}\n"
        "\n"
        "#[tokio::test]\n"
        "async fn next_flag_async() {\n"
        "    assert!(true);\n"
        "}\n",
        encoding="utf-8",
    )

    candidates = repo_map._framework_test_function_candidates(str(test_path.resolve()))

    assert candidates == ("is_escape", "next_flag_async")


def test_rust_framework_candidates_handle_visibility_and_test_attributes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    test_path = project / "tests" / "testsuite" / "attributed.rs"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "#[test]\n"
        '#[cfg(feature = "integration")]\n'
        "pub(crate) fn visible_smoke() {\n"
        "    assert!(true);\n"
        "}\n"
        "\n"
        '#[tokio::test(flavor = "multi_thread")]\n'
        "// runtime smoke test\n"
        "pub async fn async_smoke() {\n"
        "    assert!(true);\n"
        "}\n",
        encoding="utf-8",
    )

    candidates = repo_map._framework_test_function_candidates(str(test_path.resolve()))
    tokio_candidates = repo_map._rust_tokio_test_function_candidates(str(test_path.resolve()))

    assert candidates == ("visible_smoke", "async_smoke")
    assert tokio_candidates == ("async_smoke",)


def test_rust_nested_integration_tests_use_targeted_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests" / "testsuite"
    tests_dir.mkdir(parents=True)
    (project / "Cargo.toml").write_text(
        '[package]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tests_dir / "main.rs").write_text('automod::dir!("tests/testsuite");\n', encoding="utf-8")
    test_path = tests_dir / "shorts.rs"
    test_path.write_text(
        "#[test]\nfn next_flag() {\n    assert!(true);\n}\n",
        encoding="utf-8",
    )

    commands = _validation_commands(
        project,
        [test_path],
        primary_test=test_path,
        primary_symbol_name="next_flag",
        query="next_flag",
    )

    assert commands == [
        "cargo test --test testsuite next_flag",
        "cargo test --test testsuite",
        "cargo test",
    ]


# --- F3: scoped runs used to yield an EMPTY validation_plan / suggested_validation_commands
# even when the primary target resolved and a real test neighbor existed. Two seams:
# (1) `_primary_language_fallback_validation_steps` had ONLY rust/python branches, so a TS/JS
#     primary got no fallback step when the per-test validation plan came back empty (e.g. a
#     scan-ceiling-capped root on a large repo).
# (2) `_suggested_validation_command_candidates` only probed the primary file's OWN directory,
#     so a test living in a repo-ROOT test tree (not next to the source file) was invisible.


def test_primary_language_fallback_validation_steps_adds_javascript_branch(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src" / "utils"
    src_dir.mkdir(parents=True)
    _write_package_json(project, package_manager="pnpm@8.6.0")
    primary = src_dir / "withRetry.ts"
    primary.write_text("export function withRetry() {}\n", encoding="utf-8")

    steps = repo_map._primary_language_fallback_validation_steps(
        repo_root=str(src_dir),
        primary_file=str(primary),
    )

    assert steps == [
        {
            "command": "pnpm test",
            "scope": "repo",
            "runner": "javascript",
            "confidence": 0.5,
            "detection": "detected",
        }
    ]


def test_primary_language_fallback_validation_steps_javascript_suffix(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    _write_package_json(project)
    primary = src_dir / "helpers.js"
    primary.write_text("function helper() {}\nmodule.exports = { helper };\n", encoding="utf-8")

    steps = repo_map._primary_language_fallback_validation_steps(
        repo_root=str(src_dir),
        primary_file=str(primary),
    )

    assert steps == [
        {
            "command": "npm test",
            "scope": "repo",
            "runner": "javascript",
            "confidence": 0.5,
            "detection": "detected",
        }
    ]


def test_primary_language_fallback_validation_steps_no_javascript_step_without_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src" / "utils"
    src_dir.mkdir(parents=True)
    primary = src_dir / "withRetry.ts"
    primary.write_text("export function withRetry() {}\n", encoding="utf-8")

    steps = repo_map._primary_language_fallback_validation_steps(
        repo_root=str(src_dir),
        primary_file=str(primary),
    )

    assert steps == []


def test_ensure_primary_language_validation_fallback_fills_empty_js_plan(tmp_path: Path) -> None:
    """Regression for the dogfood symptom itself: the per-test validation plan came back
    completely empty (e.g. from a scan-ceiling-capped root scan on a large repo) -- simulated
    here by handing `_ensure_primary_language_validation_fallback` an empty plan directly. Before
    the fix, a TS/JS primary_file stayed empty because the fallback helper had no JS branch."""
    project = tmp_path / "project"
    src_dir = project / "src" / "utils"
    src_dir.mkdir(parents=True)
    _write_package_json(project)
    primary = src_dir / "withRetry.ts"
    primary.write_text("export function withRetry() {}\n", encoding="utf-8")

    augmented = repo_map._ensure_primary_language_validation_fallback(
        [],
        repo_root=str(src_dir),
        primary_file=str(primary),
    )

    assert augmented
    assert augmented[0]["runner"] == "javascript"
    assert augmented[0]["scope"] == "repo"
    assert augmented[0]["detection"] == "detected"
    assert augmented[0]["command"] == "npm test"


def test_suggested_validation_command_candidates_probes_root_test_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src" / "utils"
    src_dir.mkdir(parents=True)
    primary = src_dir / "withRetry.ts"

    candidates = repo_map._suggested_validation_command_candidates(primary, repo_root=project)

    assert project / "test" / "withRetry.test.ts" in candidates
    assert project / "tests" / "withRetry.test.ts" in candidates
    assert project / "__tests__" / "withRetry.test.ts" in candidates


def test_suggested_validation_command_candidates_probes_root_tests_dir_for_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src" / "foo"
    src_dir.mkdir(parents=True)
    primary = src_dir / "foo.py"

    candidates = repo_map._suggested_validation_command_candidates(primary, repo_root=project)

    assert project / "tests" / "test_foo.py" in candidates


def test_suggested_validation_command_candidates_no_duplicate_probe_when_root_is_parent(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    primary = project / "foo.py"

    candidates = repo_map._suggested_validation_command_candidates(primary, repo_root=project)

    # repo_root == source_path.parent here -- the root-tree probe must not be a redundant
    # duplicate of the parent-dir probe already in the list.
    assert candidates.count(project / "tests" / "test_foo.py") == 1


def test_agent_capsule_scoped_js_repo_yields_validation_plan_and_root_test_suggestion(
    tmp_path: Path,
) -> None:
    """End-to-end dogfood repro: a scoped `build_agent_capsule` call (path=<repo>/src/utils)
    on a TS primary with a root-level package.json and a ROOT `test/` tree neighbor must yield a
    non-empty validation_plan (the repo-scope js step) AND surface the root-tree test neighbor
    as an additive, unverified suggestion."""
    from tensor_grep.cli.agent_capsule import build_agent_capsule

    project = tmp_path / "project"
    src_dir = project / "src" / "utils"
    test_dir = project / "test"
    src_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)
    _write_package_json(project)
    (src_dir / "withRetry.ts").write_text(
        "export async function withRetry<T>(fn: () => Promise<T>): Promise<T> {\n"
        "  return fn();\n"
        "}\n",
        encoding="utf-8",
    )
    (test_dir / "withRetry.test.ts").write_text(
        'import { withRetry } from "../src/utils/withRetry";\n'
        'test("withRetry retries", () => { withRetry(async () => 1); });\n',
        encoding="utf-8",
    )

    payload = build_agent_capsule("retry helper", str(src_dir))

    assert payload["primary_target"]["file"] == str((src_dir / "withRetry.ts").resolve())
    assert payload["validation_plan"], "expected a non-empty repo-scope js validation step"
    suggested = payload["suggested_validation_commands"]
    assert suggested
    assert suggested[0]["target_test"] == "test/withRetry.test.ts"
    assert suggested[0]["verified"] is False
    # additive contract: the unverified suggestion must never leak into the strict,
    # evidence-gated validation_commands list.
    assert suggested[0]["command"] not in payload["validation_commands"]


def test_agent_capsule_scoped_python_repo_yields_root_test_suggestion(tmp_path: Path) -> None:
    from tensor_grep.cli.agent_capsule import build_agent_capsule

    project = tmp_path / "project"
    src_dir = project / "src" / "foo"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (src_dir / "foo.py").write_text("def do_thing():\n    return 1\n", encoding="utf-8")
    (tests_dir / "test_foo.py").write_text(
        "def test_do_thing():\n    assert True\n",
        encoding="utf-8",
    )

    payload = build_agent_capsule("do thing", str(src_dir))

    assert payload["validation_plan"], "expected a non-empty repo-scope pytest step"
    suggested = payload["suggested_validation_commands"]
    assert suggested
    assert suggested[0]["target_test"] == "tests/test_foo.py"
    assert suggested[0]["verified"] is False
    assert suggested[0]["command"] not in payload["validation_commands"]
