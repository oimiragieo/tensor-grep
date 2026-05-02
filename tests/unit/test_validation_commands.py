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
        ("python", [], ["uv run pytest -q"]),
        ("rust", [], ["cargo test"]),
        ("jest-unknown", ["notes/validation.txt"], ["npx jest"]),
        ("default-unknown", ["notes/validation.txt"], ["uv run pytest -q"]),
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
    if setup_kind == "python":
        src_dir = project / "src"
        src_dir.mkdir()
        (src_dir / "payments.py").write_text(
            "def create_invoice():\n    return 1\n", encoding="utf-8"
        )
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
