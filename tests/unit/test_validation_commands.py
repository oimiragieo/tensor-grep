import json
from pathlib import Path

import pytest

from tensor_grep.cli import agent_capsule, repo_map


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
        # #222 residual fix: `_raw_validation_plan_for_tests`'s "no tests" fallback now threads
        # `deadline_monotonic`/`deadline_hit` into this call (previously omitted entirely) -- the
        # stub must accept them like the real `_iter_repo_files` does, even though this test's own
        # assertions are about `root`/`max_files`, not deadline behavior.
        deadline_monotonic: float | None = None,
        deadline_hit=None,
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


# --- Dogfood #84: scoped-agent / edit-plan validation_plan parity ---------------------------
# Bug A (README boundary-trap): `_validation_repo_root`'s walk-up used to stop at the FIRST
# directory carrying even a single boundary marker (README.md/.gitignore/LICENSE/AGENTS.md)
# before ever examining that directory's parent -- so a scoped subdirectory with its own
# README.md never reached the real project root (pyproject.toml etc.), and validation discovery
# silently early-returned []. Fix A: a directory only becomes a strong `boundary_candidate` when
# it has `.git` (dir/file) OR >=2 distinct boundary markers -- a lone README no longer traps the
# walk, while a genuine repo/package boundary still does.


def test_validation_repo_root_lone_boundary_marker_does_not_trap_walk(tmp_path: Path) -> None:
    """Bug A headline repro: a README.md living IN the scoped directory used to trap the walk
    before it ever reached the root pyproject.toml two levels up."""
    project = tmp_path / "project"
    hooks_dir = project / "core" / "hooks"
    hooks_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (hooks_dir / "README.md").write_text("hooks module\n", encoding="utf-8")

    result = repo_map._validation_repo_root(hooks_dir)

    assert result == project.resolve()


def test_validation_repo_root_git_directory_alone_is_a_strong_boundary(tmp_path: Path) -> None:
    """Companion (git-top-stop): unlike a lone README, a `.git` directory by itself IS a strong
    boundary -- the walk must stop there even though a grandparent also carries a project
    marker, matching how every other tool treats `.git` as the definitive repo-root signal."""
    project = tmp_path / "project"
    hooks_dir = project / "core" / "hooks"
    hooks_dir.mkdir(parents=True)
    (project / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'outside'\nversion = '0.1.0'\n", encoding="utf-8"
    )

    result = repo_map._validation_repo_root(hooks_dir)

    assert result == project.resolve()


def test_validation_repo_root_two_boundary_markers_still_trap_walk(tmp_path: Path) -> None:
    """Companion: a directory with TWO distinct boundary markers is a genuinely strong boundary
    (e.g. a vendored subtree carrying its own README + LICENSE) and must still trap the walk --
    Fix A only exempts a LONE marker, it does not remove the boundary-trap mechanism entirely."""
    project = tmp_path / "project"
    hooks_dir = project / "core" / "hooks"
    hooks_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (hooks_dir / "README.md").write_text("hooks module\n", encoding="utf-8")
    (hooks_dir / "LICENSE").write_text("MIT\n", encoding="utf-8")

    result = repo_map._validation_repo_root(hooks_dir)

    assert result == hooks_dir.resolve()


def test_validation_repo_root_tempdir_guard_blocks_marker_above_temp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion (tempdir guard): the walk must never climb PAST the OS temp root looking for a
    marker, even when Fix A's relaxed single-marker rule would otherwise let it keep going."""
    fake_temp_root = tmp_path / "faketemp"
    fake_temp_root.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'outside'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    scoped = fake_temp_root / "scoped"
    scoped.mkdir()
    monkeypatch.setattr(repo_map.tempfile, "gettempdir", lambda: str(fake_temp_root))

    result = repo_map._validation_repo_root(scoped)

    assert result == scoped.resolve()


# Bug B (JS/TS-only discovery): `_discover_validation_tests_for_primary_file` skipped every
# candidate test file whose suffix wasn't JS/TS, so a scoped python run never found its sibling
# `tests/test_<stem>.py` even once Fix A let the walk reach the real root. Fix B: let `.py` test
# files (already `_is_test_file`-gated) fall through to the language-neutral scoring; the JS/TS
# node:test gate is untouched.


def test_discover_validation_tests_for_primary_file_finds_python_sibling_test(
    tmp_path: Path,
) -> None:
    """Bug B headline repro: with Fix A in place the walk reaches `project` (root pyproject.toml)
    from a README-carrying scoped directory, but discovery still needs Fix B to actually return
    the sibling python test instead of silently dropping every non-JS/TS candidate."""
    project = tmp_path / "project"
    scoped = project / "scoped"
    scoped.mkdir(parents=True)
    (scoped / "README.md").write_text("scoped readme\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    primary_file = scoped / "widget.py"
    primary_file.write_text("def create_widget():\n    return 1\n", encoding="utf-8")
    test_file = tests_dir / "test_widget.py"
    test_file.write_text("def test_create_widget():\n    assert True\n", encoding="utf-8")

    discovered = repo_map._discover_validation_tests_for_primary_file(
        scoped,
        str(primary_file),
        primary_symbol_name="create_widget",
        query="create widget",
        limit=5,
    )

    assert str(test_file.resolve()) in discovered


def test_discover_validation_tests_for_primary_file_still_gates_non_node_test_js_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion: Fix B must not loosen the pre-existing JS/TS node:test gate -- a JS/TS test
    file that does NOT use node:test is still excluded from discovery."""
    project = tmp_path / "project"
    scoped = project / "scoped"
    scoped.mkdir(parents=True)
    (project / "package.json").write_text("{}\n", encoding="utf-8")
    tests_dir = project / "tests"
    tests_dir.mkdir()
    primary_file = scoped / "widget.ts"
    primary_file.write_text("export function createWidget() { return 1; }\n", encoding="utf-8")
    test_file = tests_dir / "widget.test.ts"
    test_file.write_text("test('widget', () => expect(1).toBe(1));\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "_javascript_test_file_uses_node_test", lambda _path: False)

    discovered = repo_map._discover_validation_tests_for_primary_file(
        scoped,
        str(primary_file),
        primary_symbol_name="createWidget",
        query="create widget",
        limit=5,
    )

    assert str(test_file.resolve()) not in discovered


# E2E headline (both fixes together) + the edit-plan surface mirror -- Fix A + Fix B both land in
# the shared `_build_edit_plan_seed` builder, so a single fix heals `tg agent <scope>` (via
# `build_agent_capsule` -> `build_context_render`) AND `tg edit-plan` (via
# `build_context_edit_plan`) at once.


def test_agent_capsule_scoped_python_readme_trap_yields_targeted_validation_plan(
    tmp_path: Path,
) -> None:
    """E2E headline for dogfood #84: before both fixes, a scoped `tg agent core/hooks ...` run
    on a repo with a hooks-local README.md got an EMPTY validation_plan (Bug A trapped discovery
    at the scoped dir; Bug B would have dropped the python sibling test even if it hadn't)."""
    from tensor_grep.cli.agent_capsule import build_agent_capsule

    project = tmp_path / "project"
    hooks_dir = project / "core" / "hooks"
    tests_dir = project / "tests" / "hooks"
    hooks_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (hooks_dir / "README.md").write_text("hooks module\n", encoding="utf-8")
    (hooks_dir / "hook_handler.py").write_text(
        "def do_thing():\n    return True\n", encoding="utf-8"
    )
    (tests_dir / "test_hook_handler.py").write_text(
        "def test_do_thing():\n    assert True\n", encoding="utf-8"
    )

    payload = build_agent_capsule("do thing", str(hooks_dir))

    validation_plan = payload["validation_plan"]
    pytest_file_steps = [
        step
        for step in validation_plan
        if step.get("runner") == "pytest" and step.get("scope") == "file"
    ]
    assert pytest_file_steps, f"expected a file-scoped pytest step, got {validation_plan!r}"
    target = str(pytest_file_steps[0]["target"])
    assert not Path(target).is_absolute(), f"target must be root-relative, got {target!r}"
    assert pytest_file_steps[0]["detection"] == "detected"
    assert payload["validation_commands"]
    ask_reasons = payload["ask_user_before_editing"]["reasons"]
    assert "no validation command evidence" not in ask_reasons


def test_build_context_edit_plan_mirrors_scoped_python_readme_trap_fix(tmp_path: Path) -> None:
    """Finding-2 parity: `tg edit-plan` (`build_context_edit_plan`) flows through the same shared
    seed builder as `build_agent_capsule` above, so the same scoped README-trap fixture must heal
    there too."""
    project = tmp_path / "project"
    hooks_dir = project / "core" / "hooks"
    tests_dir = project / "tests" / "hooks"
    hooks_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (hooks_dir / "README.md").write_text("hooks module\n", encoding="utf-8")
    (hooks_dir / "hook_handler.py").write_text(
        "def do_thing():\n    return True\n", encoding="utf-8"
    )
    (tests_dir / "test_hook_handler.py").write_text(
        "def test_do_thing():\n    assert True\n", encoding="utf-8"
    )

    payload = repo_map.build_context_edit_plan("do thing", hooks_dir)

    validation_plan = payload["edit_plan_seed"]["validation_plan"]
    pytest_file_steps = [
        step
        for step in validation_plan
        if step.get("runner") == "pytest" and step.get("scope") == "file"
    ]
    assert pytest_file_steps, f"expected a file-scoped pytest step, got {validation_plan!r}"
    assert not Path(pytest_file_steps[0]["target"]).is_absolute()
    assert payload["validation_commands"]


# --- Dogfood v1.71.1: `tg edit-plan --json` top-level `validation_plan` parity ------------------
# `tg edit-plan --json` already exposed a FLAT top-level `validation_commands` list but dropped
# the STRUCTURED top-level `validation_plan` that `tg agent --json` already surfaces at its own
# top level -- even though the data already exists at `edit_plan_seed.validation_plan`. Additive
# fix: surface it as a top-level sibling of `validation_commands`, deep-copied so callers can
# mutate the returned steps without corrupting `edit_plan_seed`.


def test_build_context_edit_plan_surfaces_top_level_validation_plan(tmp_path: Path) -> None:
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

    payload = repo_map.build_context_edit_plan("create invoice", project)

    assert "validation_plan" in payload
    top_level_plan = payload["validation_plan"]
    assert isinstance(top_level_plan, list)
    assert top_level_plan, "expected a non-empty top-level validation_plan"
    assert all(isinstance(step, dict) and step.get("command") for step in top_level_plan)
    # Corresponds to edit_plan_seed.validation_plan -- same steps, same order.
    assert top_level_plan == payload["edit_plan_seed"]["validation_plan"]
    # Back-compat: the pre-existing flat validation_commands list is untouched, and its strings
    # are exactly the `command` field of each top-level validation_plan step.
    assert payload["validation_commands"]
    assert [step["command"] for step in top_level_plan] == payload["validation_commands"]
    # Deep-copy safety: mutating the returned top-level plan must not mutate the seed.
    top_level_plan[0]["command"] = "mutated"
    assert payload["edit_plan_seed"]["validation_plan"][0]["command"] != "mutated"


# --- CEO v1.72.1 dogfood: `tg edit-plan --json` top-level `confidence` / `ask_user_before_editing`
# parity ------------------------------------------------------------------------------------------
# `tg agent --json` already surfaces a top-level `confidence` (an `{overall, downgrade_reasons}`
# object) and an `ask_user_before_editing` gate (`{required, reasons}`); `tg edit-plan --json` had
# neither (`confidence` read as `null` via `.get`/`jq`, `ask_user_before_editing` was absent
# entirely). Computed via `agent_capsule`'s reused trust-check + `_confidence` ladder -- see
# `agent_capsule._capsule_confidence_and_ask_without_render` and
# `repo_map._edit_plan_confidence_and_ask`.


def _write_invoice_project(project: Path) -> None:
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


def test_build_context_edit_plan_surfaces_top_level_confidence_and_ask(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_invoice_project(project)

    payload = repo_map.build_context_edit_plan("create invoice", project)

    assert "confidence" in payload
    confidence = payload["confidence"]
    assert isinstance(confidence, dict)
    assert isinstance(confidence["overall"], float)
    assert confidence["overall"] is not None

    assert "ask_user_before_editing" in payload
    ask = payload["ask_user_before_editing"]
    assert isinstance(ask, dict)
    assert isinstance(ask["required"], bool)
    assert isinstance(ask["reasons"], list)


def test_build_context_edit_plan_confidence_and_ask_match_agent_for_clean_resolution(
    tmp_path: Path,
) -> None:
    """Same repo, same query: a clean (non-truncated, fully-validated) resolution should reach
    the identical baseline `tg agent --json` reaches, since both derive from the SAME reused
    `_confidence`/`_capsule_trust_checks` ladder and neither has a downgrade signal to apply."""
    project = tmp_path / "project"
    _write_invoice_project(project)
    query = "create invoice"

    edit_plan_payload = repo_map.build_context_edit_plan(query, project)
    agent_payload = agent_capsule.build_agent_capsule(query, project)

    assert edit_plan_payload["confidence"]["overall"] == agent_payload["confidence"]["overall"]
    assert edit_plan_payload["confidence"]["overall"] == 0.9
    assert edit_plan_payload["ask_user_before_editing"]["required"] is False
    assert agent_payload["ask_user_before_editing"]["required"] is False


def test_build_context_edit_plan_ask_required_when_scan_truncated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_invoice_project(project)

    payload = repo_map.build_context_edit_plan(
        "create invoice",
        project,
        max_repo_files=1,
    )

    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        "scan was truncated" in reason for reason in payload["ask_user_before_editing"]["reasons"]
    )


def test_build_context_edit_plan_ask_required_without_validation_evidence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "unrelated.py").write_text(
        "def zzz_totally_unrelated_symbol(): pass\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_edit_plan("nonexistent symbol reference", project)

    assert payload["confidence"]["overall"] is not None
    assert payload["ask_user_before_editing"]["required"] is True
    assert "no validation command evidence" in payload["ask_user_before_editing"]["reasons"]


def test_build_agent_capsule_confidence_and_ask_unchanged_by_edit_plan_parity_refactor(
    tmp_path: Path,
) -> None:
    """Golden parity test (CEO v1.72.1 dogfood): `tg agent --json`'s top-level `confidence`/
    `ask_user_before_editing` must be BYTE-IDENTICAL to their pre-refactor values, both in the
    clean case and the scan-truncated case (the exact ask-reasons-ladder lines the edit-plan
    parity fix mechanically extracted into `_capsule_validation_evidence_ask_reason` /
    `_capsule_low_confidence_ask_reason`). These expected values were captured by running
    `build_agent_capsule` against this exact fixture BEFORE the refactor landed."""
    project = tmp_path / "project"
    _write_invoice_project(project)
    query = "create invoice"

    clean_payload = agent_capsule.build_agent_capsule(query, project)
    assert clean_payload["confidence"] == {"overall": 0.9, "downgrade_reasons": []}
    assert clean_payload["ask_user_before_editing"] == {"required": False, "reasons": []}

    truncated_payload = agent_capsule.build_agent_capsule(query, project, max_repo_files=1)
    assert truncated_payload["confidence"] == {
        "overall": 0.9,
        "downgrade_reasons": [
            "repository scan truncated before ranking completed",
            "context consistency downgraded confidence",
        ],
    }
    assert truncated_payload["ask_user_before_editing"] == {
        "required": True,
        "reasons": [
            "repository scan was truncated; the ranked primary may not be the true target",
            "no validation command evidence",
            "context consistency requires confirmation",
        ],
    }


def _write_tie_project(project: Path) -> None:
    """An AMBIGUOUS repo: two sibling source files each define a symbol strongly matching the
    query, so one is ranked primary (flat 0.9 seed confidence) and the other surfaces as a
    high-confidence (1.0) alternative that TIES it -- with no test file, so no targeted validation
    evidence exists to resolve the tie. This is the exact ambiguity `tg agent` flags with
    `ask_user_before_editing.required = true`; `tg edit-plan` must match (Opus-gate MUST-FIX)."""
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (src_dir / "alpha_processor.py").write_text(
        "def process_payment_record(payload):\n    return payload\n", encoding="utf-8"
    )
    (src_dir / "beta_processor.py").write_text(
        "def process_payment_record_backup(payload):\n    return payload\n", encoding="utf-8"
    )


def test_build_context_edit_plan_flags_tie_ambiguity_matching_agent(tmp_path: Path) -> None:
    """Opus-gate safety MUST-FIX (on c63f509): `ask_user_before_editing.required` IS the hard
    auto-edit safety gate, so on an AMBIGUOUS (alternative-target-tie) plan it must match `tg
    agent` in the unsafe direction. Pre-fix, `tg edit-plan` omitted tie detection and returned
    `required = false` on the exact plan where `tg agent` returns `true` -- an agent trusting
    edit-plan would auto-edit a possibly-wrong ambiguous target without confirming.

    Tie detection needs only the payload's `candidate_edit_targets`/`file_matches` alternatives +
    `query` (NO snippets/call-site evidence), so edit-plan must compute it -- see
    `agent_capsule._capsule_confidence_and_ask_without_render`."""
    project = tmp_path / "project"
    _write_tie_project(project)
    query = "process payment record"

    edit_plan_payload = repo_map.build_context_edit_plan(query, project)
    agent_payload = agent_capsule.build_agent_capsule(query, project)

    # Agent's own contract: this fixture is a genuine confirmation tie.
    assert agent_payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert agent_payload["ask_user_before_editing"]["required"] is True

    # Parity in the UNSAFE direction: edit-plan must also gate the auto-edit.
    assert edit_plan_payload["ask_user_before_editing"]["required"] is True
    assert (
        edit_plan_payload["ask_user_before_editing"]["required"]
        == agent_payload["ask_user_before_editing"]["required"]
    )
    # The tie ask-reason is the safety signal an agent branches on.
    assert (
        "alternative target confidence ties primary target"
        in edit_plan_payload["ask_user_before_editing"]["reasons"]
    )
    # Confidence caps identically to agent on a tie (0.74, agent_capsule.py's tie branch).
    assert (
        edit_plan_payload["confidence"]["overall"] == agent_payload["confidence"]["overall"] == 0.74
    )
    assert (
        "alternative target confidence tie" in edit_plan_payload["confidence"]["downgrade_reasons"]
    )


def test_build_agent_capsule_tie_confidence_and_ask_unchanged_by_edit_plan_parity_refactor(
    tmp_path: Path,
) -> None:
    """Golden byte-unchanged pin for the AMBIGUOUS case (Opus-gate MUST-FIX): reproducing agent's
    tie/marker ladder inside edit-plan's `_capsule_confidence_and_ask_without_render` must NOT
    perturb `tg agent`'s own output (agent never calls that helper). Values captured from
    `build_agent_capsule` on this exact tie fixture."""
    project = tmp_path / "project"
    _write_tie_project(project)

    payload = agent_capsule.build_agent_capsule("process payment record", project)
    assert payload["confidence"] == {
        "overall": 0.74,
        "downgrade_reasons": ["alternative target confidence tie"],
    }
    assert payload["ask_user_before_editing"] == {
        "required": True,
        "reasons": [
            "alternative target confidence ties primary target",
            "confidence below 0.75",
            "context consistency requires confirmation",
        ],
    }
