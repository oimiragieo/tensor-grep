from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import repo_map

Renderer = Callable[[Path], dict[str, Any]]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_context(project: Path) -> dict[str, Any]:
    return repo_map.build_context_render("create invoice", project)


def _render_blast_radius(project: Path) -> dict[str, Any]:
    return repo_map.build_symbol_blast_radius_render("create_invoice", project)


RENDERERS = [
    pytest.param(_render_context, id="context"),
    pytest.param(_render_blast_radius, id="blast-radius"),
]


def _edit_plan_seed(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload["edit_plan_seed"])


def _related_span_lookup(seed: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(current["file"]), str(current["symbol"])): dict(current)
        for current in seed["related_spans"]
    }


def _build_linear_project(
    tmp_path: Path,
    *,
    include_same_file_caller: bool = False,
    include_tests: bool = True,
) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    payments_source = "def create_invoice(total):\n    return total + 1\n"
    if include_same_file_caller:
        payments_source += "\ndef local_wrapper(total):\n    return create_invoice(total)\n"

    _write(src_dir / "payments.py", payments_source)
    _write(
        src_dir / "service.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    first = create_invoice(total)\n"
        "    return create_invoice(first)\n",
    )
    _write(
        src_dir / "report.py",
        "from src.service import build_receipt\n"
        "\n"
        "def generate_report(total):\n"
        "    return build_receipt(total)\n",
    )
    _write(
        src_dir / "unrelated.py",
        "def helper():\n    return 0\n",
    )
    if include_tests:
        _write(
            tests_dir / "test_payments.py",
            "from src.payments import create_invoice\n"
            "\n"
            "def test_create_invoice():\n"
            "    assert create_invoice(1) == 2\n",
        )

    return {
        "project": project,
        "payments": src_dir / "payments.py",
        "service": src_dir / "service.py",
        "report": src_dir / "report.py",
        "unrelated": src_dir / "unrelated.py",
        "test": tests_dir / "test_payments.py",
    }


def _build_depth_chain_project(tmp_path: Path, *, include_tests: bool = True) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    _write(
        src_dir / "b.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def call_b(total):\n"
        "    return create_invoice(total)\n",
    )
    _write(
        src_dir / "c.py",
        "from src.b import call_b\n\ndef call_c(total):\n    return call_b(total)\n",
    )
    _write(
        src_dir / "d.py",
        "from src.c import call_c\n\ndef call_d(total):\n    return call_c(total)\n",
    )
    _write(
        src_dir / "e.py",
        "from src.d import call_d\n\ndef call_e(total):\n    return call_d(total)\n",
    )
    if include_tests:
        _write(
            tests_dir / "test_payments.py",
            "from src.payments import create_invoice\n"
            "\n"
            "def test_create_invoice():\n"
            "    assert create_invoice(1) == 2\n",
        )

    return {
        "project": project,
        "payments": src_dir / "payments.py",
        "b": src_dir / "b.py",
        "c": src_dir / "c.py",
        "d": src_dir / "d.py",
        "e": src_dir / "e.py",
        "test": tests_dir / "test_payments.py",
    }


def _build_caller_count_project(tmp_path: Path, *, caller_count: int) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"

    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    caller_paths: dict[str, Path] = {}
    for index in range(caller_count):
        caller_path = src_dir / f"caller_{index}.py"
        _write(
            caller_path,
            "from src.payments import create_invoice\n"
            "\n"
            f"def wrap_{index}(total):\n"
            "    return create_invoice(total)\n",
        )
        caller_paths[f"caller_{index}"] = caller_path

    return {
        "project": project,
        "payments": src_dir / "payments.py",
        **caller_paths,
    }


def _build_circular_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    _write(
        src_dir / "a.py",
        "from src.b import wrap_b\n"
        "from src.payments import create_invoice\n"
        "\n"
        "def wrap_a(total):\n"
        "    return wrap_b(create_invoice(total))\n",
    )
    _write(
        src_dir / "b.py",
        "from src.a import wrap_a\n"
        "from src.payments import create_invoice\n"
        "\n"
        "def wrap_b(total):\n"
        "    if total < 0:\n"
        "        return wrap_a(total + 1)\n"
        "    return create_invoice(total)\n",
    )
    _write(
        tests_dir / "test_cycle.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1) == 2\n",
    )

    return {
        "project": project,
        "payments": src_dir / "payments.py",
        "a": src_dir / "a.py",
        "b": src_dir / "b.py",
        "test": tests_dir / "test_cycle.py",
    }


def _build_python_depth_rank_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    examples_dir = project / "examples"

    _write(
        src_dir / "utils.py",
        "def open_file(path: str) -> str:\n    return path\n",
    )
    _write(
        src_dir / "core.py",
        "from src.utils import open_file\n\ndef use_core() -> str:\n    return open_file('core')\n",
    )
    _write(
        src_dir / "termui.py",
        "from src.utils import open_file\n"
        "\n"
        "def use_termui() -> str:\n"
        "    return open_file('termui')\n",
    )
    _write(
        src_dir / "decorators.py",
        "from src.core import use_core\n\ndef use_decorators() -> str:\n    return use_core()\n",
    )
    _write(
        examples_dir / "demo.py",
        "from src.termui import use_termui\n\ndef run_demo() -> str:\n    return use_termui()\n",
    )
    return {
        "project": project,
        "utils": src_dir / "utils.py",
        "core": src_dir / "core.py",
        "termui": src_dir / "termui.py",
        "decorators": src_dir / "decorators.py",
        "example": examples_dir / "demo.py",
    }


def _build_python_core_radius_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    examples_dir = project / "examples"

    _write(
        src_dir / "core.py",
        "class Abort(Exception):\n    pass\n",
    )
    _write(
        src_dir / "termui.py",
        "from src.core import Abort\n\ndef use_termui() -> type[Exception]:\n    return Abort\n",
    )
    _write(
        src_dir / "parser.py",
        "from src.core import Abort\n\ndef use_parser() -> type[Exception]:\n    return Abort\n",
    )
    _write(
        src_dir / "decorators.py",
        "from src.termui import use_termui\n"
        "\n"
        "def use_decorators() -> type[Exception]:\n"
        "    return use_termui()\n",
    )
    _write(
        examples_dir / "demo.py",
        "from src.parser import use_parser\n"
        "\n"
        "def run_demo() -> type[Exception]:\n"
        "    return use_parser()\n",
    )
    _write(
        tests_dir / "test_abort.py",
        "from src.termui import use_termui\n"
        "\n"
        "def test_abort_usage() -> None:\n"
        "    assert use_termui().__name__ == 'Abort'\n",
    )
    return {
        "project": project,
        "core": src_dir / "core.py",
        "termui": src_dir / "termui.py",
        "parser": src_dir / "parser.py",
        "decorators": src_dir / "decorators.py",
        "example": examples_dir / "demo.py",
        "test": tests_dir / "test_abort.py",
    }


def _build_python_termui_radius_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    examples_dir = project / "examples"

    _write(
        src_dir / "termui.py",
        "def unstyle(text: str) -> str:\n    return text\n",
    )
    _write(
        src_dir / "__init__.py",
        "from src.termui import unstyle\n",
    )
    _write(
        src_dir / "core.py",
        "from src.termui import unstyle\n\n__all__ = ['unstyle']\n",
    )
    _write(
        src_dir / "decorators.py",
        "from src.core import __all__\n\ndef expose_decorator() -> str:\n    return __all__[0]\n",
    )
    _write(
        examples_dir / "demo.py",
        "from src.decorators import expose_decorator\n"
        "\n"
        "def run_demo() -> str:\n"
        "    return expose_decorator()\n",
    )
    return {
        "project": project,
        "termui": src_dir / "termui.py",
        "init": src_dir / "__init__.py",
        "core": src_dir / "core.py",
        "decorators": src_dir / "decorators.py",
        "example": examples_dir / "demo.py",
    }


def _build_missing_symbol_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    _write(
        project / "src" / "helpers.py",
        "def helper():\n    return 1\n",
    )
    _write(
        project / "tests" / "test_helpers.py",
        "from src.helpers import helper\n\ndef test_helper():\n    assert helper() == 1\n",
    )
    return project


def _build_rust_nested_qualified_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests" / "testsuite"

    _write(
        project / "Cargo.toml",
        '[package]\nname = "sample"\nversion = "0.1.0"\n',
    )
    _write(
        src_dir / "lib.rs",
        "pub struct RawArgs;\n"
        "\n"
        "impl RawArgs {\n"
        "    pub fn new() -> Self {\n"
        "        Self\n"
        "    }\n"
        "\n"
        "    pub fn remaining(&self) -> bool {\n"
        "        true\n"
        "    }\n"
        "\n"
        "    pub fn peek_os(&self) -> bool {\n"
        "        true\n"
        "    }\n"
        "}\n"
        "\n"
        "pub struct ParsedArg;\n"
        "\n"
        "impl ParsedArg {\n"
        "    pub fn to_long(&self) -> bool {\n"
        "        true\n"
        "    }\n"
        "}\n"
        "\n"
        "pub fn parse() -> ParsedArg {\n"
        "    ParsedArg\n"
        "}\n",
    )
    _write(
        tests_dir / "main.rs",
        'automod::dir!("tests/testsuite");\n',
    )
    _write(
        tests_dir / "lexer.rs",
        "#[test]\n"
        "fn raw_args_remaining() {\n"
        "    let raw = sample::RawArgs::new();\n"
        "    assert!(raw.remaining());\n"
        "}\n",
    )
    _write(
        tests_dir / "parsed.rs",
        "#[test]\n"
        "fn parsed_to_long() {\n"
        "    let parsed = sample::parse();\n"
        "    assert!(parsed.to_long());\n"
        "}\n",
    )
    return {
        "project": project,
        "lexer_test": tests_dir / "lexer.rs",
        "parsed_test": tests_dir / "parsed.rs",
    }


@pytest.mark.parametrize("renderer", RENDERERS)
def test_edit_plan_seed_includes_enrichment_keys(tmp_path: Path, renderer: Renderer) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert "primary_span" in seed
    assert "related_spans" in seed
    assert "dependent_files" in seed
    assert "suggested_edits" in seed
    assert "edit_ordering" in seed
    assert "rollback_risk" in seed


@pytest.mark.parametrize(
    ("renderer", "symbol"),
    [
        pytest.param(
            lambda project: repo_map.build_context_render("missing symbol", project),
            "context-missing",
            id="context",
        ),
        pytest.param(
            lambda project: repo_map.build_symbol_blast_radius_render("missing_symbol", project),
            "blast-missing",
            id="blast-radius",
        ),
    ],
)
def test_edit_plan_seed_defaults_without_primary_symbol(
    tmp_path: Path,
    renderer: Renderer,
    symbol: str,
) -> None:
    project = _build_missing_symbol_project(tmp_path)

    seed = _edit_plan_seed(renderer(project))

    assert symbol
    assert seed["primary_symbol"] is None
    assert seed["primary_span"] is None
    assert seed["related_spans"] == []
    assert seed["dependent_files"] == []
    assert seed["rollback_risk"] == 0.0


@pytest.mark.parametrize("renderer", RENDERERS)
def test_existing_fields_remain_unchanged(tmp_path: Path, renderer: Renderer) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["primary_file"] in {
        str(paths["payments"].resolve()),
        str(paths["test"].resolve()),
    }
    assert seed["primary_symbol"]["name"] in {"create_invoice", "test_create_invoice"}
    assert seed["primary_test"] == str(paths["test"].resolve())
    assert seed["validation_tests"] == [str(paths["test"].resolve())]
    assert seed["validation_commands"] == [
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_related_spans_deduplicate_multiple_calls_in_same_symbol(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))
    related_spans = _related_span_lookup(seed)

    assert (str(paths["service"].resolve()), "build_receipt") in related_spans
    assert (
        len([entry for entry in seed["related_spans"] if entry["symbol"] == "build_receipt"]) == 1
    )


@pytest.mark.parametrize("renderer", RENDERERS)
def test_suggested_edits_include_dependent_file_spans_and_rationale(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["suggested_edits"]
    first = seed["suggested_edits"][0]
    assert {
        "file",
        "symbol",
        "start_line",
        "end_line",
        "edit_kind",
        "rationale",
        "confidence",
    } <= set(first)
    assert first["file"] == str(paths["service"].resolve())
    assert first["edit_kind"] in {"caller-update", "dependency-update"}
    assert isinstance(first["rationale"], str)
    assert first["rationale"]
    assert 0.0 <= first["confidence"] <= 1.0


@pytest.mark.parametrize("renderer", RENDERERS)
def test_related_spans_expose_provenance_and_span_rationale(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["related_spans"]
    related = seed["related_spans"][0]
    assert {"provenance", "rationale"} <= set(related)
    assert isinstance(related["provenance"], list)
    assert related["provenance"]
    assert isinstance(related["rationale"], str)
    assert related["rationale"]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_related_spans_include_same_file_callers_and_importers(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path, include_same_file_caller=True)

    seed = _edit_plan_seed(renderer(paths["project"]))
    related_spans = _related_span_lookup(seed)

    assert (str(paths["payments"].resolve()), "local_wrapper") in related_spans
    assert (str(paths["service"].resolve()), "build_receipt") in related_spans
    assert (str(paths["report"].resolve()), "generate_report") in related_spans


@pytest.mark.parametrize("renderer", RENDERERS)
def test_related_spans_use_symbol_catalog_lines(tmp_path: Path, renderer: Renderer) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))
    related_spans = _related_span_lookup(seed)

    assert related_spans[(str(paths["service"].resolve()), "build_receipt")] == {
        "file": str(paths["service"].resolve()),
        "symbol": "build_receipt",
        "start_line": 3,
        "end_line": 5,
        "depth": 1,
        "score": 7,
        "reasons": ["caller", "graph-depth"],
        "provenance": ["parser-backed", "graph-derived"],
        "rationale": "Selected build_receipt because it directly calls the target symbol and sits at depth 1.",
    }


@pytest.mark.parametrize("renderer", RENDERERS)
def test_dependent_files_include_callers_and_importers_but_not_unrelated(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["dependent_files"] == [
        str(paths["service"].resolve()),
        str(paths["report"].resolve()),
    ]
    assert str(paths["unrelated"].resolve()) not in seed["dependent_files"]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_python_utility_symbols_prefer_depth_one_dependents(
    tmp_path: Path, renderer: Renderer
) -> None:
    paths = _build_python_depth_rank_project(tmp_path)

    payload = renderer(paths["project"])
    if renderer is _render_context:
        payload = repo_map.build_context_render("open_file", paths["project"])
    else:
        payload = repo_map.build_symbol_blast_radius_render("open_file", paths["project"])
    seed = _edit_plan_seed(payload)

    assert seed["dependent_files"] == [
        str(paths["core"].resolve()),
        str(paths["termui"].resolve()),
    ]
    assert str(paths["decorators"].resolve()) not in seed["dependent_files"]
    assert str(paths["example"].resolve()) not in seed["dependent_files"]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_python_core_symbols_prefer_depth_one_dependents(
    tmp_path: Path, renderer: Renderer
) -> None:
    paths = _build_python_core_radius_project(tmp_path)

    payload = renderer(paths["project"])
    if renderer is _render_context:
        payload = repo_map.build_context_render("Abort", paths["project"])
    else:
        payload = repo_map.build_symbol_blast_radius_render("Abort", paths["project"])
    seed = _edit_plan_seed(payload)

    assert seed["dependent_files"] == [
        str(paths["parser"].resolve()),
        str(paths["termui"].resolve()),
    ]
    assert str(paths["decorators"].resolve()) not in seed["dependent_files"]
    assert str(paths["example"].resolve()) not in seed["dependent_files"]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_python_termui_symbols_prefer_depth_one_dependents(
    tmp_path: Path, renderer: Renderer
) -> None:
    paths = _build_python_termui_radius_project(tmp_path)

    payload = renderer(paths["project"])
    if renderer is _render_context:
        payload = repo_map.build_context_render("unstyle", paths["project"])
    else:
        payload = repo_map.build_symbol_blast_radius_render("unstyle", paths["project"])
    seed = _edit_plan_seed(payload)

    assert seed["dependent_files"] == [
        str(paths["init"].resolve()),
        str(paths["core"].resolve()),
    ]
    assert str(paths["decorators"].resolve()) not in seed["dependent_files"]
    assert str(paths["example"].resolve()) not in seed["dependent_files"]


def test_rust_blast_radius_finds_nested_tests_via_qualified_symbol_usage(tmp_path: Path) -> None:
    paths = _build_rust_nested_qualified_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render("RawArgs", paths["project"])

    assert payload["tests"] == [str(paths["lexer_test"].resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "cargo test --test testsuite raw_args_remaining",
        "cargo test --test testsuite",
        "cargo test",
    ]


def test_rust_blast_radius_finds_nested_tests_via_associated_methods(tmp_path: Path) -> None:
    paths = _build_rust_nested_qualified_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render("ParsedArg", paths["project"])

    assert payload["tests"] == [str(paths["parsed_test"].resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "cargo test --test testsuite parsed_to_long",
        "cargo test --test testsuite",
        "cargo test",
    ]


def test_rust_blast_radius_uses_impl_owner_type_for_method_test_association(tmp_path: Path) -> None:
    paths = _build_rust_nested_qualified_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render("peek_os", paths["project"])

    assert payload["tests"] == [str(paths["lexer_test"].resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "cargo test --test testsuite raw_args_remaining",
        "cargo test --test testsuite",
        "cargo test",
    ]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_edit_ordering_places_primary_first_and_tests_last(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_linear_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["edit_ordering"] == [
        str(paths["payments"].resolve()),
        str(paths["service"].resolve()),
        str(paths["report"].resolve()),
        str(paths["test"].resolve()),
    ]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_edit_ordering_is_deterministic(tmp_path: Path, renderer: Renderer) -> None:
    paths = _build_linear_project(tmp_path, include_same_file_caller=True)

    first = _edit_plan_seed(renderer(paths["project"]))
    second = _edit_plan_seed(renderer(paths["project"]))

    assert second["edit_ordering"] == first["edit_ordering"]
    assert second["related_spans"] == first["related_spans"]
    assert second["rollback_risk"] == first["rollback_risk"]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_edit_ordering_handles_circular_imports_without_duplicates(
    tmp_path: Path,
    renderer: Renderer,
) -> None:
    paths = _build_circular_project(tmp_path)

    seed = _edit_plan_seed(renderer(paths["project"]))

    assert seed["edit_ordering"][0] == str(paths["payments"].resolve())
    assert seed["edit_ordering"][-1] == str(paths["test"].resolve())
    assert seed["edit_ordering"].count(str(paths["a"].resolve())) == 1
    assert seed["edit_ordering"].count(str(paths["b"].resolve())) == 1


@pytest.mark.parametrize(
    ("max_depth", "expected_files"),
    [
        pytest.param(1, ["b"], id="depth-1"),
        pytest.param(2, ["b", "c"], id="depth-2"),
    ],
)
def test_max_depth_limits_dependent_files_and_related_spans(
    tmp_path: Path,
    max_depth: int,
    expected_files: list[str],
) -> None:
    paths = _build_depth_chain_project(tmp_path)

    payload = repo_map.build_symbol_blast_radius_render(
        "create_invoice",
        paths["project"],
        max_depth=max_depth,
    )
    seed = _edit_plan_seed(payload)
    expected_paths = [str(paths[name].resolve()) for name in expected_files]

    assert seed["dependent_files"] == expected_paths
    assert sorted({entry["file"] for entry in seed["related_spans"]}) == expected_paths


def test_rollback_risk_increases_with_depth(tmp_path: Path) -> None:
    paths = _build_depth_chain_project(tmp_path)

    depth_one = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", paths["project"], max_depth=1)
    )
    depth_three = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", paths["project"], max_depth=3)
    )

    assert depth_three["rollback_risk"] > depth_one["rollback_risk"]


def test_rollback_risk_increases_with_caller_count(tmp_path: Path) -> None:
    one_caller = _build_caller_count_project(tmp_path / "one", caller_count=1)
    many_callers = _build_caller_count_project(tmp_path / "many", caller_count=5)

    one_seed = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", one_caller["project"])
    )
    many_seed = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", many_callers["project"])
    )

    assert many_seed["rollback_risk"] > one_seed["rollback_risk"]


def test_rollback_risk_decreases_with_test_coverage(tmp_path: Path) -> None:
    untested = _build_linear_project(tmp_path / "untested", include_tests=False)
    tested = _build_linear_project(tmp_path / "tested", include_tests=True)

    untested_seed = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", untested["project"])
    )
    tested_seed = _edit_plan_seed(
        repo_map.build_symbol_blast_radius_render("create_invoice", tested["project"])
    )

    assert tested_seed["rollback_risk"] < untested_seed["rollback_risk"]


def test_context_render_full_seed_respects_capped_repo_map_file_universe(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(src_dir / "payments.py", "def create_invoice(total):\n    return total + 1\n")
    _write(
        src_dir / "service.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
    )
    outside_cap = src_dir / "z_outside_cap.py"
    _write(
        outside_cap,
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
    )
    capped_map = repo_map.build_repo_map(project, max_repo_files=2)

    payload = repo_map.build_context_render_from_map(
        capped_map,
        "create invoice",
        max_files=2,
        include_edit_plan_seed=True,
    )
    seed = _edit_plan_seed(payload)

    assert str(outside_cap.resolve()) not in capped_map["files"]
    assert str(outside_cap.resolve()) not in seed["dependent_files"]
    assert str(outside_cap.resolve()) not in {
        str(current["file"]) for current in seed["related_spans"]
    }
    assert str(outside_cap.resolve()) not in payload["candidate_edit_targets"]["files"]
    assert str(outside_cap.resolve()) not in {
        str(current["file"]) for current in payload["candidate_edit_targets"]["spans"]
    }
    assert str(outside_cap.resolve()) not in {
        str(current["file"]) for current in payload["navigation_pack"]["follow_up_reads"]
    }


def test_symbol_callers_provider_results_respect_capped_repo_map_file_universe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(src_dir / "payments.py", "def create_invoice(total):\n    return total + 1\n")
    service_path = src_dir / "service.py"
    _write(
        service_path,
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
    )
    outside_cap = src_dir / "z_outside_cap.py"
    _write(
        outside_cap,
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
    )
    capped_map = repo_map.build_repo_map(project, max_repo_files=2)

    def _external_refs(repo_root: Path, symbol: str, definitions: list[dict[str, Any]]):
        return [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(outside_cap.resolve()),
                "line": 4,
                "text": "    return create_invoice(total)",
                "provenance": "lsp-python",
            }
        ]

    monkeypatch.setattr(repo_map, "_external_references", _external_refs)

    payload = repo_map.build_symbol_callers_from_map(
        capped_map,
        "create_invoice",
        semantic_provider="hybrid",
    )
    refs_payload = repo_map.build_symbol_refs_from_map(
        capped_map,
        "create_invoice",
        semantic_provider="hybrid",
    )

    assert str(service_path.resolve()) in payload["files"]
    assert str(outside_cap.resolve()) not in payload["files"]
    assert str(outside_cap.resolve()) not in {
        str(current["file"]) for current in payload["callers"]
    }
    assert str(service_path.resolve()) in refs_payload["files"]
    assert str(outside_cap.resolve()) not in refs_payload["files"]
    assert str(outside_cap.resolve()) not in {
        str(current["file"]) for current in refs_payload["references"]
    }
