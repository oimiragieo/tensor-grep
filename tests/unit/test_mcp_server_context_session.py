"""MCP session / context / agent / orient / doctor / map / blast-radius contracts."""

import json
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from tensor_grep.cli import repo_map
from tests.unit.test_mcp_server_shared import (
    _assert_enriched_edit_plan_seed,
    _assert_navigation_pack,
    _without_profiling,
)


def test_tg_edit_plan_exposes_ranking_quality_and_coverage_summary(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_edit_plan("create invoice", str(project)))

    assert payload["ranking_quality"] in {"strong", "moderate", "weak"}
    assert {"heuristic_fields", "parser_backed_fields", "graph_completeness"} <= set(
        payload["coverage_summary"]
    )
    assert {"parser_backed", "graph_derived", "heuristic"} <= set(
        payload["coverage_summary"]["evidence_counts"]
    )
    assert {"parser_backed", "graph_derived", "heuristic"} <= set(
        payload["coverage_summary"]["evidence_ratios"]
    )
    assert payload["coverage_summary"]["evidence_counts"]["parser_backed"] >= 1
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
    assert payload["edit_plan_seed"]["dependency_trust"]["import_resolution_quality"] in {
        "strong",
        "moderate",
        "weak",
    }
    assert payload["edit_plan_seed"]["plan_trust_summary"]


def test_tg_session_context_supports_auto_refresh_alias(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server, session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    opened = session_store.open_session(str(project))
    module_path.write_text("def create_invoice():\n    return 2\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_session_context(
            opened.session_id,
            "invoice",
            str(project),
            auto_refresh=True,
        )
    )

    assert payload["routing_reason"] == "session-context"
    assert payload["files"] == [str(module_path.resolve())]


def test_tg_session_context_returns_uniform_error_detail(tmp_path: Path):
    from tensor_grep.cli import mcp_server

    missing_root = tmp_path / "missing"
    payload = json.loads(
        mcp_server.tg_session_context("session-missing", "invoice", str(missing_root))
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "detail" in payload["error"]


def test_tg_session_context_default_max_tokens_matches_sibling_context_tools(
    tmp_path: Path, monkeypatch
):
    # H4: `tg_session_context` used to call `session_context` (-> `build_context_pack_from_map`)
    # with NO token bound at all, unlike every sibling context tool. It must now default to the
    # same `_DEFAULT_MCP_CONTEXT_MAX_TOKENS` and emit the `token_budget` field.
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))

    assert payload["token_budget"]["max_tokens"] == mcp_server._DEFAULT_MCP_CONTEXT_MAX_TOKENS
    assert payload["token_budget"]["truncated"] is False


def test_tg_session_context_bounds_pack_by_max_tokens(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for i in range(6):
        (src_dir / f"mod_{i}.py").write_text(
            f"def add_{i}(x):\n    return x + {i}\n" * 20,
            encoding="utf-8",
        )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    unbounded = json.loads(
        mcp_server.tg_session_context(session_id, "add", str(project), max_tokens=0)
    )
    bounded = json.loads(
        mcp_server.tg_session_context(session_id, "add", str(project), max_tokens=50)
    )

    # 0 = explicit unbounded opt-out (matches every sibling context tool's contract).
    assert "token_budget" not in unbounded
    assert bounded["token_budget"]["max_tokens"] == 50
    assert bounded["token_budget"]["truncated"] is True
    assert len(bounded["files"]) < len(unbounded["files"])


def test_tg_session_lifecycle_errors_return_uniform_error_detail(tmp_path: Path):
    from tensor_grep.cli import mcp_server

    with (
        patch(
            "tensor_grep.cli.session_store.open_session", side_effect=RuntimeError("open failed")
        ),
        patch(
            "tensor_grep.cli.session_store.list_sessions", side_effect=RuntimeError("list failed")
        ),
        patch("tensor_grep.cli.session_store.get_session", side_effect=RuntimeError("show failed")),
        patch(
            "tensor_grep.cli.session_store.refresh_session",
            side_effect=RuntimeError("refresh failed"),
        ),
    ):
        opened = json.loads(mcp_server.tg_session_open(str(tmp_path)))
        listed = json.loads(mcp_server.tg_session_list(str(tmp_path)))
        shown = json.loads(mcp_server.tg_session_show("session-missing", str(tmp_path)))
        refreshed = json.loads(mcp_server.tg_session_refresh("session-missing", str(tmp_path)))

        for payload in (opened, listed, shown, refreshed):
            assert payload["error"]["code"] == "invalid_input"
            assert "detail" in payload["error"]


def test_tg_session_open_accepts_initial_repo_map_cap(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(4):
        (src_dir / f"module_{index}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    payload = json.loads(mcp_server.tg_session_open(str(project), max_repo_files=2))

    assert payload["schema_version"] == payload["version"]
    assert payload["file_count"] == 2
    assert payload["symbol_count"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["build_seconds"] >= 0


def test_tg_session_open_defaults_to_agent_safe_repo_map_cap(tmp_path: Path, monkeypatch):
    # #98: the default cap was raised 512 -> 2000 (mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT) so
    # tg_session_open matches every sibling MCP scan tool. Create more than 2000 files so the
    # truncation behavior at the new default cap is still exercised end-to-end (not just the
    # signature default -- see test_mcp_context_default_cap.py for the signature-level pin).
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(2005):
        (src_dir / f"module_{index:04}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    payload = json.loads(mcp_server.tg_session_open(str(project)))

    assert payload["file_count"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT == 2000
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert payload["scan_limit"]["possibly_truncated"] is True


def test_tg_session_mcp_tools_wrap_session_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]
    assert opened["file_count"] == 1

    listing = json.loads(mcp_server.tg_session_list(str(project)))
    assert listing["version"] == 1
    assert listing["sessions"][0]["session_id"] == session_id

    shown = json.loads(mcp_server.tg_session_show(session_id, str(project)))
    assert shown["session_id"] == session_id
    assert shown["repo_map"]["files"] == [str((src_dir / "sample.py").resolve())]

    context = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert (
        context["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert context["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"] == [str((src_dir / "sample.py").resolve())]


def test_tg_session_context_render_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    return x + 1\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    rendered = json.loads(mcp_server.tg_session_context_render(session_id, "add", str(project)))

    assert rendered["session_id"] == session_id
    assert rendered["routing_reason"] == "session-context-render"
    assert rendered["sources"][0]["name"] == "add"
    assert rendered["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert rendered["candidate_edit_targets"]["ranking_quality"] == rendered["ranking_quality"]
    assert rendered["candidate_edit_targets"]["coverage_summary"] == rendered["coverage_summary"]
    assert rendered["edit_plan_seed"]["validation_commands"] == []
    _assert_enriched_edit_plan_seed(
        rendered["edit_plan_seed"],
        primary_file=sample_path,
        primary_symbol_name="add",
    )
    # H6 audit: `confidence["symbol"]` is `_confidence_from_score(...)`, clamped to
    # [0.0, 1.0] (repo_map.py:10912-10915, proven load-bearing by
    # test_edit_plan_seed.py::test_confidence_from_score_clamp_is_load_bearing) -- a
    # `0.0 <= x <= 1.0` bound check can never fail. Single call site, deterministic
    # fixture: pin the exact value (verified 3x): 0.5.
    assert rendered["edit_plan_seed"]["confidence"]["symbol"] == 0.5
    assert "rendered_context" in rendered


def test_tg_session_context_render_profile_includes_profiling_without_changing_output(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    return x + 1\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    baseline = json.loads(mcp_server.tg_session_context_render(session_id, "add", str(project)))
    profiled = json.loads(
        mcp_server.tg_session_context_render(
            session_id,
            "add",
            str(project),
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == _without_profiling(baseline)


def test_tg_session_blast_radius_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

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

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(
        mcp_server.tg_session_blast_radius(
            session_id,
            "create_invoice",
            str(project),
            max_depth=1,
        )
    )

    assert payload["session_id"] == session_id
    assert payload["routing_reason"] == "session-blast-radius"
    assert payload["max_depth"] == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_tg_session_blast_radius_render_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

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

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(
        mcp_server.tg_session_blast_radius_render(
            session_id,
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )

    assert payload["session_id"] == session_id
    assert payload["routing_reason"] == "session-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["sources"][0]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "create_invoice" in payload["rendered_context"]


def test_session_serve_render_commands_include_enriched_edit_plan_seed(tmp_path):
    from tensor_grep.cli import session_store

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
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    session_id = session_store.open_session(str(project)).session_id
    stdin = StringIO(
        "\n".join([
            json.dumps({"command": "context_render", "query": "create invoice"}),
            json.dumps({
                "command": "blast_radius_render",
                "symbol": "create_invoice",
                "max_depth": 1,
            }),
        ])
        + "\n"
    )
    stdout = StringIO()

    served = session_store.serve_session_stream(
        session_id,
        str(project),
        input_stream=stdin,
        output_stream=stdout,
    )

    assert served == 2
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert responses[0]["routing_reason"] == "session-context-render"
    _assert_enriched_edit_plan_seed(responses[0]["edit_plan_seed"])
    assert responses[1]["routing_reason"] == "session-blast-radius-render"
    _assert_enriched_edit_plan_seed(
        responses[1]["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert str(service_path.resolve()) in responses[1]["edit_plan_seed"]["dependent_files"]


def test_tg_session_refresh_updates_cached_session_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    second_path = src_dir / "billing.py"
    second_path.write_text("def issue_invoice():\n    return 2\n", encoding="utf-8")

    refreshed = json.loads(mcp_server.tg_session_refresh(session_id, str(project)))
    assert refreshed["session_id"] == session_id
    assert refreshed["file_count"] == 2
    assert isinstance(refreshed["refreshed_at"], str)
    assert refreshed["refreshed_at"]

    shown = json.loads(mcp_server.tg_session_show(session_id, str(project)))
    assert str(second_path.resolve()) in shown["repo_map"]["files"]


def test_tg_session_context_reports_stale_session_until_refreshed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    sample_path.write_text("def add(x):\n    return x + 1\n", encoding="utf-8")

    stale = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert stale["error"]["code"] == "invalid_input"
    assert "changed on disk" in stale["error"]["message"]

    refreshed = json.loads(mcp_server.tg_session_refresh(session_id, str(project)))
    assert refreshed["session_id"] == session_id

    context = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"


def test_tg_session_context_can_auto_refresh_stale_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    sample_path.write_text(
        "def add(x):\n    return x\n\ndef settle_invoice():\n    return add(1)\n",
        encoding="utf-8",
    )

    context = json.loads(
        mcp_server.tg_session_context(
            session_id,
            "settle invoice",
            str(project),
            refresh_on_stale=True,
        )
    )
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert any(symbol["name"] == "settle_invoice" for symbol in context["symbols"])


def test_tg_repo_map_returns_json_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "sample.py"
    module_path.write_text(
        "import pathlib\n\nclass Widget:\n    pass\n\ndef add(x, y):\n    return x + y\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_sample.py"
    test_path.write_text("from src.sample import add\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_repo_map(str(project)))

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "repo-map"
    assert payload["sidecar_used"] is False
    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert payload["path"] == str(project.resolve())
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert payload["scan_limit"]["possibly_truncated"] is False
    assert str(module_path.resolve()) in payload["files"]
    assert str(test_path.resolve()) in payload["tests"]
    assert any(
        symbol["name"] == "Widget"
        and symbol["kind"] == "class"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "add"
        and symbol["kind"] == "function"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(module_path.resolve()) and "pathlib" in entry["imports"]
        for entry in payload["imports"]
    )
    assert str(module_path.resolve()) in payload["related_paths"]


def test_tg_repo_map_includes_typescript_and_rust_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        'import { money } from "./money";\n'
        "export class PaymentService {}\n"
        "export function createInvoice(total: number) {\n"
        "  return money(total);\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "use crate::payments::create_invoice;\n\n"
        "pub struct Invoice {}\n\n"
        "pub fn issue_invoice() -> Invoice {\n"
        "    let _ = create_invoice();\n"
        "    Invoice {}\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_repo_map(str(project)))

    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert any(
        symbol["name"] == "PaymentService"
        and symbol["kind"] == "class"
        and symbol["file"] == str(ts_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "createInvoice"
        and symbol["kind"] == "function"
        and symbol["file"] == str(ts_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "Invoice"
        and symbol["kind"] == "struct"
        and symbol["file"] == str(rust_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "issue_invoice"
        and symbol["kind"] == "function"
        and symbol["file"] == str(rust_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(ts_path.resolve()) and "./money" in entry["imports"]
        for entry in payload["imports"]
    )
    assert any(
        entry["file"] == str(rust_path.resolve())
        and "crate::payments::create_invoice" in entry["imports"]
        for entry in payload["imports"]
    )


def test_tg_orient_returns_json_capsule(tmp_path, monkeypatch):
    """audit #95 Part 2: tg_orient mirrors `tg orient --json` -> build_orient_capsule_json."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    (tmp_path / "hub.py").write_text("def hub():\n    pass\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text("import hub\n\n\ndef leaf():\n    pass\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_orient(str(tmp_path)))

    assert payload["routing_reason"] == "orient"
    assert payload["path"] == str(tmp_path.resolve())
    assert "central_files" in payload
    assert any(
        cf["file"] == str((tmp_path / "hub.py").resolve()) for cf in payload["central_files"]
    )
    assert "entry_points" in payload
    assert "snippets" in payload
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert isinstance(payload["schema_version"], int)


def test_tg_orient_docstring_directs_agent_to_call_first(tmp_path):
    """Design instruction: the docstring must close the 'run orient first is unreachable via
    MCP' gap by explicitly telling an agent to call this FIRST for orientation."""
    from tensor_grep.cli import mcp_server

    assert "call first for orientation" in (mcp_server.tg_orient.__doc__ or "").lower()


def test_tg_orient_forwards_max_tokens_max_central_files_and_ignore(monkeypatch):
    from tensor_grep.cli import mcp_server

    captured: dict[str, object] = {}

    def fake_build_orient_capsule_json(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return json.dumps({"path": path, "routing_reason": "orient"})

    monkeypatch.setattr(mcp_server, "build_orient_capsule_json", fake_build_orient_capsule_json)

    mcp_server.tg_orient(
        ".",
        max_tokens=5000,
        max_central_files=25,
        ignore=["vendor/**", "core/skills/**"],
    )

    assert captured["max_tokens"] == 5000
    assert captured["max_central_files"] == 25
    assert captured["ignore"] == ("vendor/**", "core/skills/**")


def test_tg_orient_reports_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): a relative, in-root-but-nonexistent path so the confinement check
    # (which fires first) is not what's being exercised here -- see the analogous
    # tg_index_search missing-path test above.
    out = mcp_server.tg_orient("definitely-missing-for-mcp-server-tests")

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "RepoMap"
    assert parsed["routing_reason"] == "orient"
    assert parsed["error"]["code"] == "invalid_input"
    assert "Traceback" not in parsed["error"]["message"]


def test_tg_doctor_returns_json_payload(tmp_path, monkeypatch):
    """audit #95 Part 2: tg_doctor wraps _build_doctor_payload (main.py:2790)."""
    monkeypatch.chdir(tmp_path)
    # Env-independent (A85): a polluted PATH entry that raises PermissionError on
    # ``Path.is_file()`` (seen on WSL→WindowsApps) must not fail this contract.
    monkeypatch.setenv("PATH", str(tmp_path))
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_doctor(str(tmp_path), with_lsp=False))

    assert payload["root"] == str(tmp_path.resolve())
    assert payload["config"] == str((tmp_path / "sgconfig.yml").resolve())
    assert payload["lsp"]["enabled"] is False
    assert "native_tg_binary_exists" in payload
    assert "search_acceleration_backend" in payload
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # M14 (stamping uniformity, F2-corrected): _inject_mcp_contract_fields HARD-assigns only
    # mcp_contract_version (the central const always wins over a payload's own literal); the
    # top-level "schema_version" stays setdefault so a tool's OWN domain meaning for that key
    # survives -- tg_doctor documents schema_version: 2 as the doctor JSON schema version,
    # distinct from the MCP JSON-output version (_json_output_version()). Re-clobbering it
    # would break harness_api.md-pinned consumers.
    assert payload["version"] == mcp_server._mcp_server_version()
    assert payload["schema_version"] == payload["doctor_schema_version"]


def test_tg_doctor_empty_string_config_falls_back_like_cli(tmp_path, monkeypatch):
    """Edge case for the config-confinement addition above: CLI `doctor` treats an empty
    `--config ""` as "not provided" (falls back to root/sgconfig.yml) via a plain `if config:`
    truthiness check -- config confinement must preserve that, not treat "" as a real
    (trivially in-root) value that would overwrite the default resolution."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_doctor(str(tmp_path), config="", with_lsp=False))

    assert payload["root"] == str(tmp_path.resolve())
    assert payload["config"] == str((tmp_path / "sgconfig.yml").resolve())


def test_tg_doctor_forwards_with_lsp_true_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    captured: dict[str, object] = {}

    def fake_build_doctor_payload(path, config=None, *, with_lsp):
        captured["path"] = path
        captured["config"] = config
        captured["with_lsp"] = with_lsp
        return {"root": path}

    monkeypatch.setattr(mcp_server, "_build_doctor_payload", fake_build_doctor_payload)

    mcp_server.tg_doctor(".")

    assert captured["with_lsp"] is True


def test_tg_agent_capsule_returns_actionable_context_capsule(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(project),
            max_tokens=160,
        )
    )

    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["capsule_kind"] == "actionable_context"
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["snippets"][0]["file"] == str(module_path.resolve())
    assert "subtotal = total + tax" in payload["snippets"][0]["source"]
    assert payload["snippets"][0]["line_map"][0]["line"] == 1
    assert payload["validation_commands"]
    assert payload["rollback"]["checkpoint_recommended"] is True
    assert payload["omissions"]["token_budget"] == 160
    assert "follow_up_reads" in payload["omissions"]
    assert payload["raw_context_ref"]["command"].startswith("tg context-render")
    assert payload["ask_user_before_editing"]["required"] is False


def test_tg_agent_capsule_accepts_gpu_evidence_options(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import agent_capsule, mcp_server

    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )

    def _fake_gpu_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(project),
            gpu_device_ids=[0, 1],
            gpu_timeout_s=1,
        )
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["requested_device_ids"] == [0, 1]
    assert acceleration["status"] == "unsupported"
    assert acceleration["routing_backend"] == "GpuSidecar"
    assert acceleration["sidecar_used"] is True


def test_tg_agent_capsule_returns_invalid_input_for_missing_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(tmp_path / "missing"),
        )
    )

    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["error"]["code"] == "invalid_input"
    assert "Path not found" in payload["error"]["message"]


def test_tg_edit_plan_returns_machine_readable_plan_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_edit_plan(
            "create invoice",
            str(project),
            max_files=2,
            max_repo_files=2,
            max_sources=1,
            max_tokens=64,
        )
    )

    assert payload["routing_reason"] == "context-edit-plan"
    assert payload["max_files"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["max_sources"] == 1
    assert payload["max_tokens"] == 64
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
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
    assert payload["primary_target"] == payload["navigation_pack"]["primary_target"]
    assert payload["edit_order"] == payload["edit_plan_seed"]["edit_ordering"]
    assert payload["plan"]["primary_file"] == str(module_path.resolve())
    assert payload["plan"]["primary_symbol"]["name"] == "create_invoice"
    assert "rendered_context" not in payload["plan"]


def test_tg_edit_plan_prefers_targeted_vitest_validation_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (project / "package.json").write_text(
        json.dumps({
            "name": "vitest-project",
            "devDependencies": {"vitest": "^1.0.0"},
        }),
        encoding="utf-8",
    )
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number, tax: number): number {\n"
        "  return total + tax;\n"
        "}\n",
        encoding="utf-8",
    )
    (tests_dir / "payments.test.ts").write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createInvoice } from "../src/payments";\n\n'
        'describe("payments", () => {\n'
        '  test("createInvoice adds tax", () => {\n'
        "    expect(createInvoice(1, 2)).toBe(3);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_edit_plan("create invoice", str(project)))

    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "vitest"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "symbol"
    assert payload["edit_plan_seed"]["validation_plan"][0]["command"] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )
    assert payload["edit_plan_seed"]["validation_commands"][0] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )


def test_tg_session_context_render_accepts_max_tokens_and_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    baseline = x + 1\n    return baseline\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    rendered = json.loads(
        mcp_server.tg_session_context_render(
            session_id,
            "add",
            str(project),
            max_files=1,
            max_sources=1,
            max_tokens=32,
            model="gpt-test",
        )
    )

    assert rendered["session_id"] == session_id
    assert rendered["files"][0] == str(sample_path.resolve())
    assert rendered["max_tokens"] == 32
    assert rendered["model"] == "gpt-test"
    assert isinstance(rendered["token_estimate"], int)
    assert rendered["omitted_sections"] == [] or all(
        {"file", "symbol", "score", "token_estimate"} <= set(section)
        for section in rendered["omitted_sections"]
    )


def test_tg_repo_map_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_repo_map",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_repo_map(str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_agent_capsule_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.agent_capsule.build_agent_capsule",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_agent_capsule("add", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"


def test_tg_session_edit_plan_returns_structured_error_on_unexpected_exception(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    with patch(
        "tensor_grep.cli.session_store.session_context_edit_plan",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_session_edit_plan(session_id, "add", str(project))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["session_id"] == session_id


def test_tg_context_pack_returns_ranked_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\n"
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def load_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_context_pack("invoice payment", str(project)))

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-pack"
    assert payload["sidecar_used"] is False
    assert payload["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())


def test_tg_context_render_returns_prompt_ready_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_render("create invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-render"
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["sources"][0]["name"] == "create_invoice"
    assert any(section["kind"] == "tests" for section in payload["sections"])
    assert any(section["kind"] == "source" for section in payload["sections"])
    summary_section = next(
        section for section in payload["sections"] if section["kind"] == "summary"
    )
    source_section = next(section for section in payload["sections"] if section["kind"] == "source")
    assert summary_section["provenance"]["path"] == str(module_path.resolve())
    assert "symbol" in summary_section["provenance"]["reasons"]
    assert source_section["provenance"]["symbol"] == "create_invoice"
    assert source_section["provenance"]["symbol_score"] >= 1
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["symbols"][0]["name"] == "create_invoice"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["edit_plan_seed"]["validation_tests"] == [str(test_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert payload["edit_plan_seed"]["confidence"]["file"] >= 0.5
    assert payload["edit_plan_seed"]["confidence"]["symbol"] >= 0.5
    assert payload["edit_plan_seed"]["confidence"]["test"] >= 0.5
    assert str(module_path.resolve()) in payload["rendered_context"]
    assert "create_invoice" in payload["rendered_context"]


def test_tg_context_render_profile_includes_profiling_without_changing_output(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    baseline = json.loads(mcp_server.tg_context_render("create invoice", str(project)))
    profiled = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == baseline


def test_tg_context_render_includes_exact_caller_update_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    first = create_invoice(total)\n"
        "    return create_invoice(first)\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_render("create invoice", str(project)))

    caller_updates = [
        dict(current)
        for current in payload["edit_plan_seed"]["suggested_edits"]
        if current["file"] == str(service_path.resolve())
        and current["edit_kind"] == "caller-update"
    ]
    assert [
        (entry["symbol"], entry["start_line"], entry["end_line"]) for entry in caller_updates
    ] == [
        ("build_receipt", 4, 4),
        ("build_receipt", 5, 5),
    ]
    for entry in caller_updates:
        assert entry["provenance"] == "python-ast"
        assert 0.0 < entry["confidence"] <= 1.0
        assert f"calls create_invoice() on line {entry['start_line']}" in entry["rationale"]


def test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.py"
    payments_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal: float) -> dict[str, float]:\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        '    return {"subtotal": subtotal, "tax": tax, "total": total}\n',
        encoding="utf-8",
    )
    (src_dir / "app.ts").write_text(
        "export function createInvoice(subtotal: number) {\n  return { subtotal };\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        '    assert create_invoice(100.0)["tax"] > 0\n',
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "change invoice tax calculation",
            str(project),
            render_profile="llm",
        )
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(payments_path.resolve())
    assert payload["navigation_pack"]["primary_target"]["file"] == str(payments_path.resolve())
    assert payload["sources"][0]["file"] == str(payments_path.resolve())
    assert "tax = subtotal * TAX_RATE" in payload["sources"][0]["rendered_source"]
    assert payload["context_consistency"]["primary_file_included"] is True


def test_tg_context_render_honors_max_render_chars(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    fee = subtotal + 5\n"
        "    grand_total = fee + 10\n"
        "    return grand_total\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render("create invoice", str(project), max_render_chars=120)
    )

    assert payload["truncated"] is True
    assert payload["max_render_chars"] == 120
    assert len(payload["rendered_context"]) <= 120
    assert payload["sources"][0]["name"] == "create_invoice"


def test_tg_context_render_accepts_max_tokens_and_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    fee = subtotal + 5\n"
        "    grand_total = fee + 10\n"
        "    return grand_total\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            max_files=1,
            max_sources=1,
            max_tokens=40,
            model="gpt-test",
        )
    )

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["max_tokens"] == 40
    assert payload["model"] == "gpt-test"
    assert isinstance(payload["token_estimate"], int)
    assert all(isinstance(section["token_estimate"], int) for section in payload["sections"])
    assert payload["token_estimate"] <= 40 + max(
        (section["token_estimate"] for section in payload["sections"]),
        default=0,
    )


def test_tg_context_render_can_optimize_source_blocks_for_llm_use(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "# module comment\n"
        "\n"
        "def create_invoice(total, tax):\n"
        "    # subtotal comment\n"
        "    subtotal = total + tax\n"
        "\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            optimize_context=True,
            render_profile="llm",
        )
    )

    assert payload["optimize_context"] is True
    assert payload["render_profile"] == "llm"
    source = next(item for item in payload["sources"] if item["name"] == "create_invoice")
    assert "# subtotal comment" not in source["rendered_source"]
    assert "\n\n" not in source["rendered_source"]
    assert source["line_map"][0]["original_start_line"] == 3
    assert source["line_map"][0]["rendered_start_line"] == 1
    assert source["render_diagnostics"]["removed_comment_lines"] >= 1
    assert source["render_diagnostics"]["removed_blank_lines"] >= 1
    assert "create_invoice" in payload["rendered_context"]


def test_tg_context_render_strips_python_docstrings_and_pass_boilerplate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        '    """Service docstring."""\n'
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        '    """Create an invoice."""\n'
        "    subtotal = total + tax\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    class_payload = json.loads(
        mcp_server.tg_context_render(
            "payment service",
            str(project),
            optimize_context=True,
            render_profile="compact",
        )
    )
    function_payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            optimize_context=True,
            render_profile="compact",
        )
    )

    payment_service = next(
        item for item in class_payload["sources"] if item["name"] == "PaymentService"
    )
    create_invoice = next(
        item for item in function_payload["sources"] if item["name"] == "create_invoice"
    )

    assert '"""Service docstring."""' not in payment_service["rendered_source"]
    assert "pass" not in payment_service["rendered_source"]
    assert payment_service["render_diagnostics"]["removed_docstring_lines"] >= 1
    assert payment_service["render_diagnostics"]["removed_boilerplate_lines"] >= 1

    assert '"""Create an invoice."""' not in create_invoice["rendered_source"]
    assert "subtotal = total + tax" in create_invoice["rendered_source"]
    assert create_invoice["line_map"][0]["original_start_line"] == 5
    assert create_invoice["line_map"][0]["rendered_start_line"] == 1


def test_tg_context_pack_prefers_import_linked_files_for_ranked_symbol_queries(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    notes_dir = project / "notes"
    src_dir.mkdir(parents=True)
    notes_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    importer_path = src_dir / "billing.py"
    importer_path.write_text(
        "from src.payments import create_invoice\n\ndef bill():\n    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    noisy_path = notes_dir / "invoice_notes.py"
    noisy_path.write_text("def placeholder():\n    return 'invoice'\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_context_pack("create invoice", str(project)))

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["files"][1] == str(importer_path.resolve())
    assert str(noisy_path.resolve()) not in payload["files"][:2]
    assert payload["file_matches"][0]["path"] == str(module_path.resolve())
    assert "symbol" in payload["file_matches"][0]["reasons"]
    assert "definition" in payload["file_matches"][0]["reasons"]
    assert payload["file_matches"][1]["path"] == str(importer_path.resolve())
    assert "import" in payload["file_matches"][1]["reasons"]
    assert any(
        entry["file"] == str(importer_path.resolve()) and entry["provenance"] == "python-ast"
        for entry in payload["imports"]
    )
    assert payload["file_summaries"][0]["path"] == str(module_path.resolve())
    assert {item["name"] for item in payload["file_summaries"][0]["symbols"]} == {"create_invoice"}


def test_tg_context_pack_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_context_pack",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_context_pack("add", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_context_pack_prefers_more_central_importers_over_tied_leaf_importers(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    central_path = src_dir / "z_billing.py"
    central_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    leaf_path = src_dir / "a_cli.py"
    leaf_path.write_text(
        "from src.payments import create_invoice\n\ndef run():\n    return create_invoice(2, 3)\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.py"
    ui_path.write_text(
        "from src.z_billing import invoice_total\n\ndef render():\n    return invoice_total()\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.z_billing import invoice_total\n\ndef serve():\n    return invoice_total()\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_pack("create invoice", str(project)))

    assert payload["files"].index(str(central_path.resolve())) < payload["files"].index(
        str(leaf_path.resolve())
    )
    central_match = next(
        item for item in payload["file_matches"] if item["path"] == str(central_path.resolve())
    )
    leaf_match = next(
        item for item in payload["file_matches"] if item["path"] == str(leaf_path.resolve())
    )
    assert "graph-centrality" in central_match["reasons"]
    assert central_match["graph_score"] > leaf_match["graph_score"]
