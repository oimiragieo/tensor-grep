import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, repo_map
from tensor_grep.cli.main import (
    app,
)
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_agent_capsule_unrequested_marker_helper_tie_requires_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "project"
    python_path = project / "src" / "tensor_grep" / "cli" / "main.py"
    rust_path = project / "rust_core" / "src" / "python_sidecar.rs"
    python_path.parent.mkdir(parents=True)
    rust_path.parent.mkdir(parents=True)
    python_path.write_text(
        "def _write_windows_exe_bridge_marker(root):\n    return root / 'tg.com'\n",
        encoding="utf-8",
    )
    rust_path.write_text(
        "pub fn is_managed_windows_exe_bridge(path: &std::path::Path) -> bool {\n    true\n}\n",
        encoding="utf-8",
    )

    validation_plan = [
        {
            "command": "uv run pytest tests/unit/test_cli_modes.py -q",
            "runner": "pytest",
            "detection": "detected",
        }
    ]

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(python_path),
                    "symbol": "_write_windows_exe_bridge_marker",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                },
                "follow_up_reads": [],
                "validation_commands": [validation_plan[0]["command"]],
            },
            "edit_plan_seed": {
                "primary_file": str(python_path),
                "primary_symbol": {
                    "name": "_write_windows_exe_bridge_marker",
                    "kind": "function",
                },
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.9},
                "validation_plan": validation_plan,
                "validation_commands": [validation_plan[0]["command"]],
                "edit_ordering": [str(python_path)],
            },
            "candidate_edit_targets": {
                "symbols": [
                    {
                        "file": str(rust_path),
                        "name": "is_managed_windows_exe_bridge",
                        "kind": "function",
                        "line": 1,
                        "score": 90,
                    }
                ]
            },
            "file_matches": [
                {
                    "path": str(rust_path),
                    "score": 90,
                    "reasons": ["source"],
                    "provenance": ["parser-backed"],
                }
            ],
            "validation_commands": [validation_plan[0]["command"]],
            "sources": [
                {
                    "file": str(python_path),
                    "symbol": "_write_windows_exe_bridge_marker",
                    "start_line": 1,
                    "end_line": 2,
                    "source": python_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_context_render_from_map", _fake_context_render
    )

    payload = agent_capsule.build_agent_capsule(
        "harden Windows subprocess exe bridge",
        project,
        max_tokens=400,
    )

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ask_user_before_editing"]["required"] is True
    # The unrequested marker-helper primary is now PROMOTED to the implementation candidate
    # (is_managed_windows_exe_bridge); the marker is demoted to a tied alternative, so the
    # ambiguity is still flagged for confirmation (the safety contract holds). Previously the
    # marker stayed primary with an "unrequested marker helper" downgrade reason.
    assert payload["primary_target"]["symbol"] == "is_managed_windows_exe_bridge"


def test_agent_capsule_exact_camel_symbol_stays_above_snake_case_bridge(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "createInvoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["typescript"].resolve())
    assert payload["primary_target"]["symbol"] == "createInvoice"
    assert payload["context_consistency"]["primary_target_language"] == "typescript"


def test_agent_capsule_exact_snake_symbol_keeps_python_target(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "create_invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["primary_target_language"] == "python"


def test_agent_capsule_conflicting_language_and_exact_symbol_requires_confirmation(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python createInvoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["typescript"].resolve())
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "typescript"
    assert payload["confidence"]["overall"] <= 0.55
    assert payload["primary_target"]["confidence"] <= 0.55
    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        "language intent" in reason for reason in payload["ask_user_before_editing"]["reasons"]
    )


def test_query_language_hints_are_token_bounded() -> None:
    assert repo_map._query_language_hints("python invoice tax") == ["python"]
    assert repo_map._query_language_hints("py ts js rs") == [
        "python",
        "typescript",
        "javascript",
        "rust",
    ]
    assert repo_map._query_language_hints("cryptography typescriptish") == []
    # M13 audit: all 10 registered languages are now aliasable so the capsule's mismatch-cap /
    # candidate filter can fire for them (previously go/java/php/csharp/c/cpp -> [] = fail-open).
    assert repo_map._query_language_hints("go function name") == ["go"]
    assert repo_map._query_language_hints("golang handler") == ["go"]
    assert repo_map._query_language_hints("java class builder") == ["java"]
    assert repo_map._query_language_hints("php array map") == ["php"]
    assert repo_map._query_language_hints("csharp record type") == ["csharp"]
    assert repo_map._query_language_hints("cpp template class") == ["cpp"]
    assert repo_map._query_language_hints("c struct pointer") == ["c"]


def test_context_render_filters_pytest_only_validation_for_typescript_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "context-render",
            "--query",
            "createInvoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["typescript"].resolve())
    assert payload["edit_plan_seed"]["validation_plan"] == []
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["validation_commands"] == []
    assert payload["navigation_pack"]["validation_commands"] == []
    alignment = payload["edit_plan_seed"]["validation_alignment"]
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("pytest" in issue for issue in alignment["issues"])
    assert payload["context_consistency"]["validation_filtered_count"] >= 1


def test_edit_plan_filters_pytest_only_validation_for_typescript_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "edit-plan",
            "--query",
            "createInvoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["typescript"].resolve())
    assert payload["edit_plan_seed"]["validation_plan"] == []
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["validation_commands"] == []
    assert payload["navigation_pack"]["validation_commands"] == []
    alignment = payload["edit_plan_seed"]["validation_alignment"]
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("pytest" in issue for issue in alignment["issues"])


def test_validation_alignment_uses_primary_file_when_primary_symbol_is_missing(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    ts_primary = src_dir / "app.ts"
    ts_primary.write_text("export const invoiceTotal = 1;\n", encoding="utf-8")
    py_test = tests_dir / "test_payments.py"
    py_test.write_text("def test_invoice_total():\n    assert True\n", encoding="utf-8")

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(py_test)],
        repo_root=project,
        primary_test=str(py_test),
        primary_symbol=None,
        primary_file=ts_primary,
        query="invoice total",
    )

    assert plan == []
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1


def test_validation_alignment_filters_javascript_commands_for_python_primary_file(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    python_primary = src_dir / "payments.py"
    python_primary.write_text("TAX_RATE = 0.08\n", encoding="utf-8")
    ts_test = tests_dir / "payments.test.ts"
    ts_test.write_text(
        'import { test } from "vitest";\ntest("invoice tax", () => {\n  expect(1).toBe(1);\n});\n',
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(ts_test)],
        repo_root=project,
        primary_test=str(ts_test),
        primary_symbol=None,
        primary_file=python_primary,
        query="python invoice tax",
    )

    assert plan == []
    assert alignment["primary_target_language"] == "python"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("vitest" in issue for issue in alignment["issues"])


def test_agent_capsule_filters_pytest_only_validation_for_typescript_primary(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    ts_path = project / "src" / "app.ts"
    ts_path.parent.mkdir()
    ts_path.write_text(
        "export function createInvoice(subtotal: number): number {\n  return subtotal;\n}\n",
        encoding="utf-8",
    )
    test_path = project / "tests" / "test_payments.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_create_invoice():\n    assert True\n", encoding="utf-8")

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(ts_path),
                    "symbol": "createInvoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                },
                "follow_up_reads": [],
                "validation_commands": [f"uv run pytest {test_path} -q"],
            },
            "edit_plan_seed": {
                "primary_file": str(ts_path),
                "primary_symbol": {"name": "createInvoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 3},
                "confidence": {"overall": 0.94},
                "validation_plan": [
                    {
                        "command": f"uv run pytest {test_path} -q",
                        "scope": "file",
                        "runner": "pytest",
                        "target": str(test_path),
                        "confidence": 0.82,
                        "detection": "detected",
                    }
                ],
                "validation_commands": [f"uv run pytest {test_path} -q"],
                "edit_ordering": [str(ts_path)],
            },
            "validation_commands": [f"uv run pytest {test_path} -q"],
            "sources": [
                {
                    "file": str(ts_path),
                    "symbol": "createInvoice",
                    "start_line": 1,
                    "end_line": 3,
                    "source": ts_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_context_render_from_map", _fake_context_render
    )

    payload = agent_capsule.build_agent_capsule(
        "createInvoice tax calculation",
        project,
        max_tokens=400,
    )

    assert payload["primary_target"]["file"] == str(ts_path)
    assert payload["validation_plan"] == []
    assert payload["validation_commands"] == []
    assert payload["context_consistency"]["validation_alignment"]["status"] == "mismatch-filtered"
    assert payload["context_consistency"]["validation_filtered_count"] == 1
    assert payload["confidence"]["overall"] <= 0.65
    assert payload["primary_target"]["confidence"] <= 0.65
    assert payload["ask_user_before_editing"]["required"] is True
    assert any("validation" in reason for reason in payload["ask_user_before_editing"]["reasons"])


def test_agent_capsule_json_preserves_original_line_map_after_compaction(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    # bookkeeping noise\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    snippet = next(
        item for item in payload["snippets"] if item["file"] == str(module_path.resolve())
    )
    assert "# bookkeeping noise" not in snippet["source"]
    assert "subtotal = total + tax" in snippet["source"]
    assert snippet["line_map"] == [
        {"line": 1, "text": "def create_invoice(total, tax):"},
        {"line": 3, "text": "    subtotal = total + tax"},
        {"line": 4, "text": "    return subtotal"},
    ]


def test_agent_capsule_json_reports_omissions_and_follow_up_reads_when_budget_is_tight(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(3):
        (src_dir / f"invoice_{index}.py").write_text(
            f"def create_invoice_{index}(total, tax):\n"
            f"    subtotal = total + tax + {index}\n"
            "    return subtotal\n",
            encoding="utf-8",
        )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "invoice",
            "--max-tokens",
            "40",
            "--max-files",
            "3",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["omissions"]["omitted_section_count"] >= 1
    assert payload["omissions"]["follow_up_reads"]
    assert all(
        "tg source" in item["command"] or "tg context-render" in item["command"]
        for item in payload["omissions"]["follow_up_reads"]
    )
    assert all("argv" in item for item in payload["omissions"]["follow_up_reads"])
    assert payload["confidence"]["overall"] < 0.95


def test_agent_capsule_json_emits_argv_safe_recovery_commands_for_spaced_paths(tmp_path):
    project = tmp_path / "project with spaces"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            'change invoice "tax" calculation',
            "--max-tokens",
            "1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    raw_ref = payload["raw_context_ref"]
    assert raw_ref["argv"] == [
        "tg",
        "context-render",
        str(project.resolve()),
        'change invoice "tax" calculation',
        "--json",
        "--max-files",
        "3",
        "--max-sources",
        "5",
        "--max-tokens",
        "1",
        "--max-repo-files",
        # backlog #1: --max-repo-files default raised 512 -> 2000 for routing accuracy.
        "2000",
    ]
    assert f'"{project.resolve()}"' in raw_ref["command"]
    assert "--query" not in raw_ref["argv"]
    rollback = payload["rollback"]
    assert rollback["argv"] == ["tg", "checkpoint", "create", str(project.resolve())]
    assert f'"{project.resolve()}"' in rollback["command"]
    follow_up_reads = payload["omissions"]["follow_up_reads"]
    assert follow_up_reads
    source_reads = [read for read in follow_up_reads if read["argv"][:2] == ["tg", "source"]]
    assert source_reads
    assert any(
        read["argv"] == ["tg", "source", str(module_path.resolve()), "create_invoice", "--json"]
        for read in source_reads
    )
    assert all("--symbol" not in read["argv"] for read in source_reads)
    assert any(f'"{module_path.resolve()}"' in read["command"] for read in source_reads)


def test_agent_capsule_json_requires_user_confirmation_without_validation_commands(tmp_path):
    src_dir = tmp_path / "standalone"
    src_dir.mkdir()
    (src_dir / "helper.py").write_text(
        "def update_helper(value):\n    return value.strip()\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "update standalone helper",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["validation_commands"] == []
    assert payload["validation_plan"] == []
    assert payload["ask_user_before_editing"]["required"] is True
    assert "no validation command evidence" in payload["ask_user_before_editing"]["reasons"]


def test_agent_capsule_does_not_emit_pytest_for_bare_tests_dir_and_doc_primary(tmp_path):
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    readme_path = project / "README.md"
    readme_path.write_text("Validate documentation updates here.\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "validate docs",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(readme_path.resolve())
    assert payload["validation_commands"] == []
    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        reason.startswith("no validation command evidence")
        for reason in payload["ask_user_before_editing"]["reasons"]
    )


def test_agent_capsule_json_reports_primary_consistency_and_downgrades_when_primary_is_omitted(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_path = src_dir / "invoice.py"
    primary_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total\n"
        + "".join(f"    subtotal = subtotal + tax + {index}\n" for index in range(80))
        + "    return subtotal\n",
        encoding="utf-8",
    )
    secondary_path = src_dir / "related.py"
    secondary_path.write_text(
        'def invoice_note():\n    return "invoice"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "create invoice tax",
            "--max-tokens",
            "12",
            "--max-files",
            "2",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "context_consistency" in payload
    assert payload["primary_target"]["file"] == str(primary_path.resolve())
    assert payload["context_consistency"]["primary_file"] == payload["primary_target"]["file"]
    assert payload["snippets"]
    assert str(primary_path.resolve()) not in {snippet["file"] for snippet in payload["snippets"]}
    assert str(secondary_path.resolve()) in {snippet["file"] for snippet in payload["snippets"]}
    assert payload["context_consistency"]["capsule_primary_file_in_snippets"] is False
    assert payload["context_consistency"]["capsule_primary_file_in_follow_up_reads"] is True
    assert payload["context_consistency"]["capsule_primary_file_omitted"] is True
    assert payload["confidence"]["overall"] < 0.75
    assert (
        "primary file omitted from capsule snippets by token budget"
        in payload["confidence"]["downgrade_reasons"]
    )
    assert payload["ask_user_before_editing"]["required"] is True
    assert (
        "primary file omitted from capsule snippets"
        in payload["ask_user_before_editing"]["reasons"]
    )


def test_agent_capsule_text_summary_names_primary_target_and_validation(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["agent", "--query", "change invoice tax calculation", str(project)],
    )

    assert result.exit_code == 0, result.output
    assert "Agent capsule for" in result.stdout
    assert "primary=" in result.stdout
    assert "validation=" in result.stdout
    assert "confidence=" in result.stdout
    assert "ask_required=" in result.stdout
    assert "ambiguity=" in result.stdout
    assert "alternatives=" in result.stdout


def test_build_context_render_can_skip_edit_plan_seed_for_bounded_roots(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "hybrid-search.cjs"
    module_path.write_text(
        "function HybridSearch() {\n  return 'ok';\n}\n",
        encoding="utf-8",
    )
    seen: dict[str, bool] = {"called": False}

    def _unexpected_attach(*args, **kwargs):
        seen["called"] = True
        raise AssertionError("_attach_edit_plan_metadata should be skipped for bounded roots")

    monkeypatch.setattr(repo_map, "_attach_edit_plan_metadata", _unexpected_attach)

    payload = repo_map.build_context_render(
        "hybrid search",
        project,
        max_repo_files=25,
        include_edit_plan_seed=False,
    )

    assert payload["routing_reason"] == "context-render"
    assert payload["edit_plan_seed"] == {}
    assert payload["edit_plan_seed_skipped"] is True
    assert payload["navigation_pack"]["primary_target"]["file"] == str(module_path.resolve())
    assert payload["navigation_pack"]["validation_commands"] == []
    assert seen["called"] is False


def test_build_context_render_full_seed_reuses_bounded_repo_map(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (src_dir / "z_outside_cap.py").write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("bounded context-render must not recrawl the physical repo")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_repo_files=2,
        include_edit_plan_seed=True,
        max_files=2,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["dependent_files"] == [str(service_path.resolve())]
    assert unbounded_walks == 0


def test_build_context_edit_plan_uses_bounded_repo_map(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (src_dir / "z_outside_cap.py").write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("bounded edit-plan must not recrawl the physical repo")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_context_edit_plan(
        "create invoice",
        project,
        max_repo_files=2,
        max_files=2,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["dependent_files"] == [str(service_path.resolve())]
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert unbounded_walks == 0


def test_build_context_edit_plan_caps_file_summary_symbols(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "\n".join(
            [
                "def create_invoice(total):",
                "    return total + 1",
                "",
                "def invoice_tax(total):",
                "    return total * 0.1",
                "",
                "def invoice_discount(total):",
                "    return total - 1",
                "",
                "def invoice_receipt(total):",
                "    return str(total)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = repo_map.build_context_edit_plan(
        "invoice",
        project,
        max_files=1,
        max_symbols=2,
        max_sources=1,
    )

    assert payload["file_summaries"][0]["path"] == str(module_path.resolve())
    assert len(payload["file_summaries"][0]["symbols"]) <= 2


def test_context_render_skips_blast_radius_for_low_confidence_fuzzy_symbol(
    monkeypatch, tmp_path: Path
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "session_store.py"
    module_path.write_text(
        "def _empty_changeset():\n    return {'added': [], 'modified': [], 'removed': []}\n",
        encoding="utf-8",
    )

    def _unexpected_blast_radius(*_args, **_kwargs):
        raise AssertionError("low-confidence fuzzy symbols should not build blast radius")

    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_from_map",
        _unexpected_blast_radius,
    )

    payload = repo_map.build_context_render(
        "change invoice tax calculation",
        project,
        include_edit_plan_seed=True,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "_empty_changeset"
    assert payload["edit_plan_seed"]["dependent_files"] == []


def test_context_render_json_reports_bounded_repo_scan(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    (project / "first.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (project / "second.py").write_text("def second():\n    return 2\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "first",
            "--max-repo-files",
            "1",
            "--json",
            str(project),
        ],
    )

    # --max-repo-files 1 scan-truncates the context-render -> exit 2 (Cluster B exit-code contract).
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["scan_limit"] == {
        "max_repo_files": 1,
        "scanned_files": 1,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        # ADDED #336: exact-dict assertions exist so a new field must be declared DELIBERATELY.
        # `project-files` is the budget cap, so True is right; an `unreadable-path` cap emits False.
        "budget_remediable": True,
    }


def test_context_render_json_includes_markdown_file_sources(tmp_path):
    runner = CliRunner()
    docs = tmp_path / "docs"
    docs.mkdir()
    guide_path = docs / "routing_policy.md"
    guide_path.write_text(
        "# Routing Policy\n\n"
        "The routing policy explains how tensor-grep chooses native CPU, rg, and GPU paths.\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "routing policy native GPU",
            "--json",
            str(docs),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ranking_quality"] == "strong"
    assert payload["sources"][0]["kind"] == "file"
    assert payload["sources"][0]["file"] == str(guide_path.resolve())
    assert "Routing Policy" in payload["rendered_context"]


def test_context_render_llm_profile_omits_full_inventories(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(8):
        (src_dir / f"worker_{index}.py").write_text(
            f"def create_invoice_{index}(total):\n"
            f"    subtotal = total + {index}\n"
            "    return subtotal\n",
            encoding="utf-8",
        )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=2,
        max_sources=2,
        optimize_context=True,
        render_profile="llm",
    )

    assert payload["render_profile"] == "llm"
    assert payload["context_payload_profile"] == "llm-compact"
    assert "symbols" not in payload
    assert "imports" not in payload
    assert "related_paths" not in payload
    assert "candidate_edit_targets" not in payload
    assert "file_matches" not in payload
    assert "file_summaries" not in payload
    assert "test_matches" not in payload
    assert all("source" not in source for source in payload["sources"])
    assert all("rendered_source" in source for source in payload["sources"])
    assert payload["navigation_pack"]["primary_target"]["file"] in payload["files"]


def test_context_render_llm_profile_compacts_agent_metadata(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "target.py"
    target_path.write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    for index in range(12):
        (src_dir / f"caller_{index}.py").write_text(
            "from src.target import create_invoice\n\n"
            f"def caller_{index}(total):\n"
            "    return create_invoice(total)\n",
            encoding="utf-8",
        )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=1,
        max_sources=1,
        max_render_chars=1200,
        optimize_context=True,
        render_profile="llm",
    )

    assert payload["context_payload_profile"] == "llm-compact"
    assert "validation_commands" in payload
    assert payload["validation_commands"] == payload["navigation_pack"]["validation_commands"]
    assert len(payload["edit_plan_seed"]["edit_ordering"]) <= 2
    assert len(payload["navigation_pack"]["edit_ordering"]) <= 2
    assert len(payload["edit_plan_seed"]["related_spans"]) <= 1
    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 1
    # H4 audit: the seed now carries the REQUIRED `confidence.overall` key (a handful of bytes),
    # and this 9000 guard was ALREADY a knife-edge (~5-byte margin, #525 CI) whose result shifts
    # with the runner's tmp_path length across platforms (the documented byte-test platform
    # lesson -- locally ~7.7k, CI win/mac ~9.0k). Keep a robust margin that still catches a
    # broken (un-compacted) payload; the structural asserts above carry the compaction substance.
    assert len(json.dumps(payload)) < 12_000


def test_context_render_json_defaults_to_agent_compact_payload(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--max-files",
            "1",
            "--max-sources",
            "1",
            "--max-render-chars",
            "1200",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "llm"
    assert payload["context_payload_profile"] == "llm-compact"
    assert "source" not in payload["sources"][0]
    assert "validation_commands" in payload


def test_context_render_json_llm_profile_uses_compact_wire_format(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "llm"
    assert '\n  "' not in result.stdout
    assert len(result.stdout) < len(json.dumps(payload, indent=2))


def test_context_render_profile_exposes_public_profile_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=1,
        max_sources=1,
        render_profile="llm",
        profile=True,
    )

    assert "profile" in payload
    assert payload["profile"]["enabled"] is True
    assert payload["profile"]["total_elapsed_s"] >= 0
    assert payload["_profiling"]["total_elapsed_s"] == payload["profile"]["total_elapsed_s"]


def test_blast_radius_json_supports_output_limits(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n"
        + "\n".join(f"def helper_{index}():\n    return {index}\n" for index in range(12)),
        encoding="utf-8",
    )
    for index in range(6):
        (src_dir / f"caller_{index}.py").write_text(
            "from src.target import create_invoice\n\n"
            f"def caller_{index}(total):\n"
            "    return create_invoice(total)\n",
            encoding="utf-8",
        )

    result = runner.invoke(
        app,
        [
            "blast-radius",
            "--symbol",
            "create_invoice",
            "--max-callers",
            "2",
            "--max-files",
            "2",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["callers"]) <= 2
    assert len(payload["files"]) <= 2
    assert len(payload["file_matches"]) <= 2
    assert len(payload["import_graph_consumers"]) <= 2
    assert all(len(level.get("files", [])) <= 2 for level in payload["caller_tree"])
    assert all(
        path in payload["files"]
        for level in payload["caller_tree"]
        for path in level.get("files", [])
    )
    assert all(len(summary.get("symbols", [])) <= 3 for summary in payload["file_summaries"])
    assert all(path in payload["rendered_caller_tree"] for path in payload["files"])
    assert payload["output_limit"] == {
        "max_callers": 2,
        "max_files": 2,
        "callers_truncated": True,
        "files_truncated": True,
        "import_consumers_truncated": True,
        "total_callers": 6,
        "returned_callers": 2,
        "omitted_callers": 4,
        "total_files": 7,
        "returned_files": 2,
        "omitted_files": 5,
        "total_import_consumers": 6,
        "returned_import_consumers": 1,
        "omitted_import_consumers": 5,
    }


def test_blast_radius_json_defaults_to_bounded_agent_output(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def safe_parse_json(value):\n    return value\n",
        encoding="utf-8",
    )
    for index in range(60):
        (src_dir / f"caller_{index:02}.py").write_text(
            "from src.target import safe_parse_json\n\n"
            f"def caller_{index}(value):\n"
            "    return safe_parse_json(value)\n",
            encoding="utf-8",
        )

    result = runner.invoke(
        app,
        [
            "blast-radius",
            "--symbol",
            "safe_parse_json",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["callers"]) <= 25
    assert len(payload["files"]) <= 25
    assert payload["output_limit"]["max_callers"] == 25
    assert payload["output_limit"]["max_files"] == 25
    assert payload["output_limit"]["total_callers"] == 60
    assert payload["output_limit"]["omitted_callers"] == 35
    assert len(result.stdout.encode("utf-8")) < 80_000


def test_blast_radius_caller_scan_prefilters_files_without_symbol_literal(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "target.py"
    target_path.write_text(
        "def safe_parse_json(value):\n    return value\n",
        encoding="utf-8",
    )
    caller_paths = []
    for index in range(3):
        caller_path = src_dir / f"caller_{index}.py"
        caller_path.write_text(
            "from src.target import safe_parse_json\n\n"
            f"def caller_{index}(value):\n"
            "    return safe_parse_json(value)\n",
            encoding="utf-8",
        )
        caller_paths.append(caller_path.resolve())
    for index in range(40):
        (src_dir / f"unrelated_{index}.py").write_text(
            f"def unrelated_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    scanned_python_files: list[Path] = []
    original_python_references_and_calls = repo_map._python_references_and_calls

    def _tracked_python_references_and_calls(path: Path, symbol: str):
        scanned_python_files.append(path.resolve())
        return original_python_references_and_calls(path, symbol)

    monkeypatch.setattr(
        repo_map,
        "_python_references_and_calls",
        _tracked_python_references_and_calls,
    )

    payload = repo_map.build_symbol_blast_radius(
        "safe_parse_json",
        project,
        max_repo_files=1000,
        max_callers=2,
        max_files=2,
    )

    allowed_scans = {target_path.resolve(), *caller_paths}
    assert set(scanned_python_files) <= allowed_scans
    assert len(payload["callers"]) <= 2
    assert payload["output_limit"]["callers_truncated"] is True


def test_commonjs_repo_map_extracts_exported_function_symbols(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "worker.cjs"
    module_path.write_text(
        "const fs = require('fs');\n"
        "\n"
        "function prepareCursorWorkerInvocation(input) {\n"
        "  return input;\n"
        "}\n"
        "\n"
        "module.exports = {\n"
        "  prepareCursorWorkerInvocation,\n"
        "  safeParseJSON: function safeParseJSON(value) {\n"
        "    return JSON.parse(value);\n"
        "  },\n"
        "  runCursorWorker: async function runCursorWorker() {\n"
        "    return prepareCursorWorkerInvocation({});\n"
        "  },\n"
        "};\n"
        "\n"
        "exports.waitForHandoff = async function waitForHandoff() {\n"
        "  return true;\n"
        "};\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    names = {str(symbol["name"]) for symbol in payload["symbols"]}
    assert {
        "prepareCursorWorkerInvocation",
        "safeParseJSON",
        "runCursorWorker",
        "waitForHandoff",
    } <= names
    assert not any(name.startswith(("module", "exports", "function")) for name in names)

    source_payload = repo_map.build_symbol_source("safeParseJSON", project)
    assert source_payload["sources"][0]["file"] == str(module_path.resolve())
    assert "return JSON.parse(value);" in source_payload["sources"][0]["source"]


def test_js_repo_map_uses_byte_safe_symbol_names(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "unicode-prefix.mjs"
    module_path.write_text(
        "const label = 'ééé';\nclass Engine {\n  run() {\n    return label;\n  }\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    names = {str(symbol["name"]) for symbol in payload["symbols"]}
    assert "Engine" in names
    assert not any(" " in name or "{" in name or "\n" in name for name in names)


def test_test_only_repo_map_keeps_files_non_empty_for_agent_inventory(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_worker.py"
    test_path.write_text("def test_worker():\n    assert True\n", encoding="utf-8")

    payload = repo_map.build_repo_map(tests_dir)

    assert payload["files"] == [str(test_path.resolve())]
    assert payload["tests"] == [str(test_path.resolve())]
    assert payload["related_paths"] == [str(test_path.resolve())]


def test_iter_repo_files_does_not_resolve_every_child_file(monkeypatch, tmp_path):
    # L8/repo-map: the gitignore-aware walk matches paths AS WALKED against the
    # once-resolved root, so it must NOT call path.resolve() on every child — that would
    # be an O(files) stat/symlink syscall regression on large trees (~384k files on a
    # workspace root). Resolving the root itself is fine; resolving children is not.
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("print('ok')\n", encoding="utf-8")
    expected = project.resolve() / "a.py"
    original_resolve = repo_map.Path.resolve

    def _guarded_resolve(self, *args, **kwargs):
        if self.name == "a.py":
            raise AssertionError("repo-map walk must not resolve() child files")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(repo_map.Path, "resolve", _guarded_resolve)

    files = repo_map._iter_repo_files(project)

    assert files == [expected]


def test_repo_map_file_universe_does_not_resolve_child_files(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    project_root = project.resolve()
    child_file = project_root / "a.py"
    child_file.write_text("print('ok')\n", encoding="utf-8")
    original_resolve = repo_map.Path.resolve

    def _guarded_resolve(self, *args, **kwargs):
        if self.name == "a.py":
            raise AssertionError("repo-map child paths should preserve map identity")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(repo_map.Path, "resolve", _guarded_resolve)

    files = repo_map._repo_map_file_universe(
        {
            "path": str(project_root),
            "files": [str(child_file)],
            "tests": [],
        }
    )

    assert files == [child_file]


def test_detect_validation_runners_caps_repo_scan(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    seen: dict[str, object] = {}
    original_iter_repo_files = repo_map._iter_repo_files

    def _wrapped_iter(root, **kwargs):
        seen["max_files"] = kwargs.get("max_files")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _wrapped_iter)

    repo_map._detect_validation_runners.cache_clear()
    try:
        repo_map._detect_validation_runners(str(project))
    finally:
        repo_map._detect_validation_runners.cache_clear()

    assert seen["max_files"] == repo_map._VALIDATION_RUNNER_SCAN_LIMIT


def test_edit_plan_json_returns_machine_readable_plan_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["edit-plan", "--query", "create invoice", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "context-edit-plan"
    # backlog #1: --max-repo-files default raised 512 -> 2000 for routing accuracy.
    assert payload["scan_limit"]["max_repo_files"] == 2000
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    assert payload["candidate_edit_targets"]["spans"][0]["depth"] == 0
    assert payload["primary_target"] == payload["navigation_pack"]["primary_target"]
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["edit_order"] == payload["edit_plan_seed"]["edit_ordering"]
    assert payload["plan"]["primary_file"] == str(module_path.resolve())
    assert payload["plan"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["plan"]["edit_order"] == payload["edit_order"]
    assert "rendered_context" not in payload["plan"]
    assert "sources" not in payload["plan"]
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    _assert_navigation_pack(
        payload["navigation_pack"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "validation_commands" in payload
    assert payload["validation_commands"] == payload["navigation_pack"]["validation_commands"]
    assert payload["validation_commands"] == payload["edit_plan_seed"]["validation_commands"]


def test_edit_plan_json_accepts_agent_budget_flags(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create invoice",
            "--max-files",
            "2",
            "--max-repo-files",
            "2",
            "--max-sources",
            "1",
            "--max-tokens",
            "64",
            "--json",
            str(project),
        ],
    )

    # --max-repo-files 2 scan-truncates this fixture -> exit 2 (Cluster B exit-code contract);
    # the payload (with the accepted budget flags) is still emitted before the exit.
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["max_files"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["max_sources"] == 1
    assert payload["max_tokens"] == 64
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert len(payload["edit_plan_seed"]["related_spans"]) <= 1
    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 1
    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())


def test_blast_radius_render_json_returns_prompt_ready_radius_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "blast-radius-render",
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--max-render-chars",
            "400",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 1
    assert payload["sources"][0]["name"] == "create_invoice"
    assert any(section["kind"] == "source" for section in payload["sections"])
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert str(module_path.resolve()) in payload["rendered_context"]
    assert "create_invoice" in payload["rendered_context"]


def test_blast_radius_plan_json_returns_machine_readable_radius_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "blast-radius-plan",
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-blast-radius-plan"
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_edit_plan_json_prefers_targeted_vitest_validation_commands(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "vitest-project",
                "devDependencies": {"vitest": "^1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number, tax: number): number {\n"
        "  return total + tax;\n"
        "}\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "payments.test.ts"
    test_path.write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createInvoice } from "../src/payments";\n\n'
        'describe("payments", () => {\n'
        '  test("createInvoice adds tax", () => {\n'
        "    expect(createInvoice(1, 2)).toBe(3);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create invoice",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)

    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "vitest"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "symbol"
    assert payload["edit_plan_seed"]["validation_plan"][0]["command"] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )
    assert payload["edit_plan_seed"]["validation_commands"][0] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )


def test_edit_plan_json_prefers_ancestor_package_script_for_nested_ts_subdir(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    package_root = project / "packages" / "core"
    nested_src_dir = package_root / "src" / "tools"
    tests_dir = package_root / "tests"
    nested_src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (package_root / "package.json").write_text(
        json.dumps(
            {
                "name": "nested-vitest-project",
                "devDependencies": {"vitest": "^1.0.0"},
                "scripts": {"test": "vitest run"},
            }
        ),
        encoding="utf-8",
    )
    module_path = nested_src_dir / "glob.ts"
    module_path.write_text(
        "export function createGlobMatcher(pattern: string): string {\n  return pattern;\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "glob.test.ts").write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createGlobMatcher } from "../src/tools/glob";\n\n'
        'describe("glob", () => {\n'
        '  test("createGlobMatcher returns the input pattern", () => {\n'
        '    expect(createGlobMatcher("*.ts")).toBe("*.ts");\n'
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create glob matcher",
            "--json",
            str(nested_src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "javascript"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "repo"
    assert payload["edit_plan_seed"]["validation_commands"][0] == "npm test"


def test_edit_plan_json_omits_js_fallback_for_manifest_free_tsx_subdir(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "FileWriteToolDiff.tsx"
    module_path.write_text(
        'export function FileWriteToolDiff(): string {\n  return "diff";\n}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "file write diff",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["edit_plan_seed"]["validation_plan"] == []


def test_edit_plan_json_does_not_escape_manifest_free_repo_boundary(tmp_path):
    runner = CliRunner()
    outer_root = tmp_path / "outer"
    external_root = outer_root / "copied-agent"
    src_dir = external_root / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    (outer_root / "pyproject.toml").write_text(
        "[project]\nname = 'outer'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (external_root / "README.md").write_text("# copied agent\n", encoding="utf-8")
    (external_root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    module_path = src_dir / "FileWriteToolDiff.tsx"
    module_path.write_text(
        "export function FileWriteToolDiff(): string {\n"
        '  return "file write diff read before write token budget";\n'
        "}\n",
        encoding="utf-8",
    )
    sibling_path = src_dir / "FileWritePermissionRequest.tsx"
    sibling_path.write_text(
        "export function FileWritePermissionRequest(): string {\n"
        '  return "file write permission request read before write token budget";\n'
        "}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "file write diff read before write token budget",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["edit_plan_seed"]["validation_plan"] == []


def test_edit_plan_json_prefers_js_repo_fallback_over_pytest_for_mixed_repo_without_tests(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    cli_dir = project / ".claude" / "tools" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "agent-studio-like",
                "packageManager": "pnpm@10.0.0",
                "scripts": {"test": "pnpm test"},
            }
        ),
        encoding="utf-8",
    )
    (project / "scripts").mkdir()
    (project / "scripts" / "helper.py").write_text(
        "def helper():\n    return True\n", encoding="utf-8"
    )
    module_path = cli_dir / "hybrid-search.cjs"
    module_path.write_text(
        "function supportsDaemonCommand(command) {\n"
        "  return command !== '--help' && command !== '-h';\n"
        "}\n"
        "function shouldUseDaemon(command) {\n"
        "  if (!supportsDaemonCommand(command)) return false;\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "hybrid search daemon command",
            "--json",
            str(cli_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"][0] == "pnpm test"
    assert "uv run pytest -q" not in payload["edit_plan_seed"]["validation_commands"]


def test_navigation_pack_prefetches_single_same_directory_related_read_into_primary_phase(tmp_path):
    from tensor_grep.cli import repo_map

    src_dir = tmp_path / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "FileWriteToolDiff.tsx"
    sibling_path = src_dir / "FileWritePermissionRequest.tsx"
    module_path.write_text(
        "export function FileWriteToolDiff(): string { return 'diff'; }\n", encoding="utf-8"
    )
    sibling_path.write_text(
        "export function FileWritePermissionRequest(): string { return 'request'; }\n",
        encoding="utf-8",
    )

    payload = {
        "edit_plan_seed": {
            "primary_file": str(module_path.resolve()),
            "primary_symbol": {"name": "FileWriteToolDiff"},
            "primary_span": {"start_line": 1, "end_line": 1},
            "reasons": ["primary-symbol"],
            "confidence": {"overall": 0.9},
            "validation_tests": [],
            "validation_commands": ["npm test"],
            "edit_ordering": [str(module_path.resolve()), str(sibling_path.resolve())],
            "rollback_risk": 0.2,
        },
        "candidate_edit_targets": {
            "spans": [
                {
                    "file": str(module_path.resolve()),
                    "symbol": "FileWriteToolDiff",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "primary",
                },
                {
                    "file": str(sibling_path.resolve()),
                    "symbol": "FileWritePermissionRequest",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
            ]
        },
    }

    navigation_pack = repo_map._navigation_pack({}, payload, max_reads=4)

    groups = navigation_pack["parallel_read_groups"]
    assert len(groups) == 1
    assert groups[0]["label"] == "primary"
    assert sorted(groups[0]["roles"]) == ["primary", "related"]
    assert str(module_path.resolve()) in groups[0]["files"]
    assert str(sibling_path.resolve()) in groups[0]["files"]
