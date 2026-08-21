import json
import subprocess
import time
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, repo_map
from tensor_grep.cli.main import (
    app,
)
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_blast_radius_skips_build_artifacts_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    build_dir = project / "rust_core" / "target" / "debug"
    source_dir = project / "src" / "tensor_grep" / "cli"
    build_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(5):
        (build_dir / f"artifact_{index}.rs").write_text(
            f"fn generated_{index}() {{}}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "main.py"
    source_file.write_text(
        "def main_entry() -> None:\n    pass\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "main_entry",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_defers_root_files_before_bounded_source_scan(tmp_path):
    project = tmp_path / "project"
    source_dir = project / "src"
    source_dir.mkdir(parents=True)
    for index in range(5):
        (project / f"root_note_{index}.md").write_text(
            f"# root clutter {index}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "worker.py"
    source_file.write_text(
        "def runCursorWorker() -> None:\n    pass\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "runCursorWorker",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_samples_sibling_source_trees_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    claude_dir = project / ".claude" / "tools"
    source_dir = project / "scripts" / "agents"
    claude_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(8):
        (claude_dir / f"tool_{index}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "worker.cjs"
    source_file.write_text(
        "function prepareCursorWorkerInvocation(input) {\n  return input;\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "prepareCursorWorkerInvocation",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_seeds_literal_symbol_file_when_source_bucket_hits_cap(tmp_path):
    project = tmp_path / "project"
    source_dir = project / ".claude" / "lib"
    source_dir.mkdir(parents=True)
    for index in range(20):
        (source_dir / f"aaa_unrelated_{index:02}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "zzz_safe_parse.cjs"
    source_file.write_text(
        "function safeParseJSON(value) {\n  return JSON.parse(value);\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "safeParseJSON",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert str(source_file.resolve()) in payload["scan_limit"]["literal_seed_files"]


def test_blast_radius_literal_seed_scan_stays_bounded(monkeypatch, tmp_path):
    project = tmp_path / "project"
    source_dir = project / ".claude" / "lib"
    source_dir.mkdir(parents=True)
    for index in range(20):
        (source_dir / f"aaa_unrelated_{index:02}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "zzz_safe_parse.cjs"
    source_file.write_text(
        "function safeParseJSON(value) {\n  return JSON.parse(value);\n}\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if Path(root).resolve() == project.resolve() and kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("literal symbol seed scan must stay bounded")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_symbol_blast_radius(
        "safeParseJSON",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert unbounded_walks == 0


def test_blast_radius_output_limit_reports_omitted_counts():
    payload = {
        "symbol": "safeParseJSON",
        "callers": [{"file": f"caller_{index}.cjs"} for index in range(4)],
        "caller_tree": [{"depth": 1, "files": [f"caller_{index}.cjs" for index in range(4)]}],
        "files": [f"file_{index}.cjs" for index in range(5)],
        "file_matches": [{"path": f"file_{index}.cjs"} for index in range(5)],
        "file_summaries": [{"path": f"file_{index}.cjs", "symbols": []} for index in range(5)],
        "tests": [],
        "test_matches": [],
        "related_paths": [f"file_{index}.cjs" for index in range(5)],
        "symbols": [],
        "imports": [],
    }

    limited = repo_map._apply_blast_radius_output_limits(
        payload,
        max_callers=2,
        max_files=3,
    )

    assert limited["output_limit"] == {
        "max_callers": 2,
        "max_files": 3,
        "callers_truncated": True,
        "files_truncated": True,
        "import_consumers_truncated": False,
        "total_callers": 4,
        "returned_callers": 2,
        "omitted_callers": 2,
        "total_files": 5,
        "returned_files": 3,
        "omitted_files": 2,
        "total_import_consumers": 0,
        "returned_import_consumers": 0,
        "omitted_import_consumers": 0,
    }


def test_context_render_json_includes_enriched_edit_plan_seed_fields(tmp_path):
    runner = CliRunner()
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

    result = runner.invoke(
        app,
        ["context-render", "--query", "create invoice", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "context-render"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_agent_context_commands_accept_path_query_positional_alias(tmp_path):
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
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "context": "context-pack",
        "context-render": "context-render",
        "agent": "agent-context-capsule",
        "edit-plan": "context-edit-plan",
    }

    for command, routing_reason in commands.items():
        result = runner.invoke(app, [command, str(project), "create invoice", "--json"])
        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["query"] == "create invoice"
        assert _has_expected_file(payload)


def test_agent_context_commands_warn_for_legacy_query_option(tmp_path):
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
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "context": "context-pack",
        "context-render": "context-render",
        "agent": "agent-context-capsule",
        "edit-plan": "context-edit-plan",
    }

    for command, routing_reason in commands.items():
        result = runner.invoke(
            app,
            [command, "--query", "create invoice", str(project), "--json"],
        )
        assert result.exit_code == 0, result.output
        assert f"Warning: --query is deprecated for tg {command}" in result.stderr
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["query"] == "create invoice"
        assert _has_expected_file(payload)


def test_agent_context_help_hides_legacy_query_option():
    runner = CliRunner()

    for command in ("context", "context-render", "agent", "edit-plan"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--query" not in _strip_ansi(result.stdout)


def test_agent_context_commands_reject_positional_and_flag_query(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    result = CliRunner().invoke(
        app,
        ["edit-plan", str(project), "create invoice", "--query", "other", "--json"],
    )

    assert result.exit_code == 1
    assert "Use either positional QUERY or --query" in result.output


def test_route_test_json_compares_context_render_and_edit_plan_targets(tmp_path):
    runner = CliRunner()
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

    result = runner.invoke(app, ["route-test", str(project), "create invoice tax"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "route-test"
    assert payload["query"] == "create invoice tax"
    assert payload["agreement"] is True
    assert payload["agreement_details"] == {"file": True, "symbol": True, "line": True}
    assert payload["warnings"] == []
    assert payload["context_render"]["routing_reason"] == "context-render"
    assert payload["edit_plan"]["routing_reason"] == "context-edit-plan"

    for command_key in ("context_render", "edit_plan"):
        target = payload[command_key]["primary_target"]
        assert target["file"] == str(module_path.resolve())
        assert target["symbol"] == "create_invoice"
        assert target["line"] == 1
        assert target["confidence_score"] >= 0.75
        assert payload[command_key]["validation_command_count"] == 3

    assert payload["validation_command_counts"] == {
        "context_render": 3,
        "edit_plan": 3,
    }


def test_route_test_json_warns_on_disagreement_and_low_confidence(monkeypatch, tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    context_file = project / "context.py"
    edit_file = project / "edit.py"
    context_file.write_text("def context_target():\n    return 1\n", encoding="utf-8")
    edit_file.write_text("def edit_target():\n    return 2\n", encoding="utf-8")

    def fake_context_render(*_args, **_kwargs):
        return {
            "routing_reason": "context-render",
            "navigation_pack": {
                "primary_target": {
                    "file": str(context_file.resolve()),
                    "symbol": "context_target",
                    "line": 1,
                    "confidence": {"file": 0.7, "symbol": 0.9},
                },
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **_kwargs):
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": {
                "file": str(edit_file.resolve()),
                "symbol": "edit_target",
                "line": 1,
                "confidence": {"file": 0.9, "symbol": 0.9},
            },
            "validation_commands": ["pytest"],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    result = runner.invoke(app, ["route-test", str(project), "ambiguous target"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["agreement"] is False
    assert payload["agreement_details"] == {"file": False, "symbol": False, "line": True}
    assert any("primary targets disagree" in warning for warning in payload["warnings"])
    assert any(
        "context-render primary target confidence 0.700" in warning
        for warning in payload["warnings"]
    )


def test_route_test_json_demotes_low_confidence_to_note_when_routes_agree(monkeypatch, tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    def _agreeing_target():
        return {
            "file": str(target_file.resolve()),
            "symbol": "create_invoice",
            "line": 1,
            "confidence": {"file": 0.65, "symbol": 0.9},
        }

    def fake_context_render(*_args, **_kwargs):
        return {
            "routing_reason": "context-render",
            "navigation_pack": {"primary_target": _agreeing_target(), "validation_commands": []},
        }

    def fake_edit_plan(*_args, **_kwargs):
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _agreeing_target(),
            "validation_commands": ["pytest"],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    result = runner.invoke(app, ["route-test", str(project), "add invoice"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["agreement"] is True
    # Routes agree -> a sub-threshold confidence is ranking calibration, NOT a routing warning.
    assert not any("is below" in warning for warning in payload["warnings"])
    assert any("ranking-score calibration" in note for note in payload["notes"])
    assert any("confidence 0.650 is below" in note for note in payload["notes"])


def test_route_test_is_publicly_visible_in_top_level_help():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "route-test" in help_text


def test_route_test_help_still_documents_its_own_options():
    runner = CliRunner()

    result = runner.invoke(app, ["route-test", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "--max-files" in help_text
    assert "--provider" in help_text
    assert "--deadline" in help_text
    assert "--no-deadline" in help_text


def test_route_test_threads_shared_deadline_monotonic_into_both_builders(monkeypatch, tmp_path):
    """#223: route-test must anchor ONE deadline_monotonic and pass the SAME value to both the
    context-render and edit-plan builders (not recompute an independent N-second budget per
    side) -- otherwise side 2 would silently get its own full budget instead of whatever wall
    clock side 1 left, defeating the shared-anchor SLA design."""
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    captured: dict[str, float | None] = {}

    def fake_context_render(*_args, **kwargs):
        captured["context_render"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-render",
            "navigation_pack": {
                "primary_target": _route_test_agreeing_target(target_file),
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **kwargs):
        captured["edit_plan"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _route_test_agreeing_target(target_file),
            "validation_commands": [],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    before = time.monotonic()
    result = runner.invoke(app, ["route-test", str(project), "add invoice", "--deadline", "30"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    assert captured["context_render"] is not None
    assert captured["edit_plan"] is not None
    # Shared anchor: BOTH sides receive the IDENTICAL absolute deadline, not independently
    # recomputed 30-seconds-from-now values (which would differ by however long side 1 took).
    assert captured["context_render"] == captured["edit_plan"]
    assert before + 30 <= captured["context_render"] <= after + 30


def test_route_test_default_deadline_matches_agent_cold_default(monkeypatch, tmp_path):
    """#223: with no explicit --deadline/--no-deadline, route-test must default to the SAME 60s
    constant `tg agent`'s cold path uses (agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS) --
    not a second, independently-invented default that could silently drift from it. route-test
    pays the full context-render + edit-plan cost twice (dogfood v19: ~27s alone, tripped a 60s
    external harness timeout under concurrent load), unlike context-render/edit-plan which stay
    unbounded by default."""
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    captured: dict[str, float | None] = {}

    def fake_context_render(*_args, **kwargs):
        captured["context_render"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-render",
            "navigation_pack": {
                "primary_target": _route_test_agreeing_target(target_file),
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **kwargs):
        captured["edit_plan"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _route_test_agreeing_target(target_file),
            "validation_commands": [],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    before = time.monotonic()
    result = runner.invoke(app, ["route-test", str(project), "add invoice"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    assert captured["context_render"] == captured["edit_plan"]
    default_seconds = agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS
    assert before + default_seconds <= captured["context_render"] <= after + default_seconds


def test_route_test_no_deadline_disables_bound(monkeypatch, tmp_path):
    """#223: --no-deadline must disable the default bound entirely -- both sides get None
    (unbounded), not the 60s default."""
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    captured: dict[str, object] = {"context_render": "unset", "edit_plan": "unset"}

    def fake_context_render(*_args, **kwargs):
        captured["context_render"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-render",
            "navigation_pack": {
                "primary_target": _route_test_agreeing_target(target_file),
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **kwargs):
        captured["edit_plan"] = kwargs.get("deadline_monotonic")
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _route_test_agreeing_target(target_file),
            "validation_commands": [],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    result = runner.invoke(app, ["route-test", str(project), "add invoice", "--no-deadline"])

    assert result.exit_code == 0, result.output
    assert captured["context_render"] is None
    assert captured["edit_plan"] is None


def test_route_test_json_stamps_partial_and_exits_2_when_a_side_truncates(monkeypatch, tmp_path):
    """#223 HONESTY: when either builder's payload stamps partial=True (a real --deadline cutoff
    upstream, build_context_render_from_map / build_context_edit_plan_from_map's own return-time
    backstop), route-test's OWN composite payload must stamp partial=True, agreement_basis=
    "partial", and exit 2 -- an agreement computed from a truncated side must never masquerade as
    a full-confidence verdict just because the two sides happen to still agree. Deterministic at
    the unit level (mirrors the #669/#671 monkeypatched-clock pattern's determinism goal without
    needing a real clock: route-test does not own the deadline-TRIPPING mechanism, only the
    aggregation/honesty-propagation of an already-partial builder payload, so faking the builder
    return value isolates exactly the logic this fix adds)."""
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    def fake_context_render(*_args, **_kwargs):
        return {
            "routing_reason": "context-render",
            "partial": True,
            "partial_reason": "deadline",
            "deadline_limit": {"deadline_exceeded": True},
            "navigation_pack": {
                "primary_target": _route_test_agreeing_target(target_file),
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **_kwargs):
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _route_test_agreeing_target(target_file),
            "validation_commands": ["pytest"],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    result = runner.invoke(app, ["route-test", str(project), "add invoice", "--deadline", "5"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    # The agreement verdict itself is still computed and reported (both sides genuinely agree
    # here) -- what changes is the trust label attached to it.
    assert payload["agreement"] is True
    assert payload["partial"] is True
    assert payload["partial_reason"] == "deadline"
    assert payload["agreement_basis"] == "partial"
    assert payload["deadline_limit"] == {
        "deadline_exceeded": True,
        "context_render": True,
        "edit_plan": False,
    }


def test_route_test_text_mode_reports_partial_and_exits_2(monkeypatch, tmp_path):
    """#223: the --text branch gained the SAME exit-2-on-truncation contract as --json (it had NO
    exit-2 logic at all before this fix) and prints a one-line partial tell instead of silently
    exiting 0 on a truncated comparison."""
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    target_file = project / "target.py"
    target_file.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    def fake_context_render(*_args, **_kwargs):
        return {
            "routing_reason": "context-render",
            "partial": True,
            "partial_reason": "deadline",
            "navigation_pack": {
                "primary_target": _route_test_agreeing_target(target_file),
                "validation_commands": [],
            },
        }

    def fake_edit_plan(*_args, **_kwargs):
        return {
            "routing_reason": "context-edit-plan",
            "primary_target": _route_test_agreeing_target(target_file),
            "validation_commands": ["pytest"],
        }

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)
    monkeypatch.setattr(repo_map, "build_context_edit_plan", fake_edit_plan)

    result = runner.invoke(
        app, ["route-test", str(project), "add invoice", "--deadline", "5", "--text"]
    )

    assert result.exit_code == 2, result.output
    assert "agreement=True" in result.output
    assert "partial=true agreement_basis=partial" in result.output


def test_route_test_json_no_pressure_omits_partial_fields(tmp_path):
    """#223 byte-identity guard: when neither side hits the (now-defaulted) deadline, route-test's
    JSON payload must carry none of the new additive fields -- partial, partial_reason,
    agreement_basis, deadline_limit are ALL absent, matching the pre-#223 shape exactly (the
    default 60s bound is present internally but never trips on this tiny fixture)."""
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["route-test", str(project), "create invoice tax"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for key in ("partial", "partial_reason", "agreement_basis", "deadline_limit"):
        assert key not in payload, f"unexpected additive key {key!r} on a no-pressure run"


def test_agent_capsule_json_returns_actionable_context_capsule(tmp_path):
    runner = CliRunner()
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
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--max-tokens",
            "160",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["capsule_version"] == 1
    assert payload["capsule_kind"] == "actionable_context"
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["snippets"][0]["file"] == str(module_path.resolve())
    assert "subtotal = total + tax" in payload["snippets"][0]["source"]
    assert payload["snippets"][0]["line_map"][0]["line"] == 1
    assert payload["related_call_sites"] == []
    assert payload["validation_commands"]
    assert payload["edit_order"][0] == str(module_path.resolve())
    assert [row["command"] for row in payload["validation_plan"]] == payload["validation_commands"]
    assert all("detection" in row for row in payload["validation_plan"])
    assert payload["rollback"]["checkpoint_recommended"] is True
    assert payload["omissions"]["token_budget"] == 160
    assert "follow_up_reads" in payload["omissions"]
    assert payload["raw_context_ref"]["command"].startswith("tg context-render")
    assert payload["ask_user_before_editing"]["required"] is False
    # DAR (arxiv steal #4): `create_invoice` here has no outbound calls (its body is a single
    # arithmetic expression), so the capsule must emit NEITHER outbound-dependency key.
    assert "outbound_dependencies" not in payload
    assert "outbound_dependency_evidence" not in payload


def test_agent_capsule_collects_bounded_call_site_evidence_for_explicit_symbol(tmp_path):
    runner = CliRunner()
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
    service_path = src_dir / "billing.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def settle_invoice():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change create_invoice tax calculation",
            "--max-files",
            "2",
            "--max-tokens",
            "500",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload["call_site_evidence"]["symbol"] == "create_invoice"
    assert payload["call_site_evidence"]["returned_call_sites"] >= 1
    assert payload["call_site_evidence"]["max_callers"] == 4
    call_site_files = {row["file"] for row in payload["related_call_sites"]}
    assert str(service_path.resolve()) in call_site_files
    assert str(test_path.resolve()) in call_site_files
    assert all(row["line"] >= 1 for row in payload["related_call_sites"])
    assert all(
        row["reason"] == "direct caller of primary target" for row in payload["related_call_sites"]
    )
    # PathA STAGE T1: ref_kind is additive on related_call_sites too -- every row here is a real
    # `create_invoice(...)` call site, so ref_kind must be "call".
    assert all(row.get("ref_kind") == "call" for row in payload["related_call_sites"])
    assert any(item["strategy"] == "blast-radius-call-sites" for item in payload["route_rationale"])


def test_agent_capsule_reports_no_call_sites_without_heuristic_proof(
    monkeypatch,
    tmp_path,
):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    def _empty_blast_radius(*_args, **kwargs):
        return {
            "routing_reason": "symbol-blast-radius",
            "callers": [],
            "output_limit": {"omitted_callers": 0},
            "graph_trust_summary": {"trust": "strong"},
            "max_callers": kwargs.get("max_callers"),
        }

    monkeypatch.setattr(agent_capsule.repo_map, "build_symbol_blast_radius", _empty_blast_radius)

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change create_invoice tax calculation",
            "--max-files",
            "2",
            "--max-tokens",
            "500",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["call_site_evidence"]["status"] == "collected_no_call_sites"
    assert payload["call_site_evidence"]["returned_call_sites"] == 0
    assert payload["call_site_evidence"]["provenance"] == []
    assert not any(
        item["strategy"] == "blast-radius-call-sites" for item in payload["route_rationale"]
    )


def test_agent_capsule_skips_call_site_collection_when_symbol_not_explicit(
    monkeypatch,
    tmp_path,
):
    runner = CliRunner()
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
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    def _fail_unbounded_collection(*_args, **_kwargs):
        raise AssertionError("fuzzy capsule query should not collect call-site evidence")

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_symbol_blast_radius", _fail_unbounded_collection
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--max-tokens",
            "500",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["related_call_sites"] == []
    assert payload["call_site_evidence"] == {
        "status": "skipped",
        "reason": "primary symbol was not explicitly requested by query",
    }


def test_agent_capsule_gpu_evidence_uses_native_route(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "payments.py"
    target_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        if len(calls) == 1:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "routing_reason": "gpu-device-ids-explicit-native",
                "sidecar_used": False,
                "total_matches": 1,
                "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
            }
        else:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "routing_reason": "gpu-device-ids-explicit-native",
                "sidecar_used": False,
                "total_matches": 2,
                "matches": [
                    {
                        "file": str(target_path.resolve()),
                        "line": 1,
                        "text": "def create_invoice(total, tax):",
                        "pattern_text": "invoice",
                    }
                ],
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "used"
    assert acceleration["requested_device_ids"] == [0]
    assert acceleration["routing_backend"] == "NativeGpuBackend"
    assert acceleration["sidecar_used"] is False
    assert acceleration["matched_files"] == [str(target_path.resolve())]
    assert payload["context_consistency"]["gpu_evidence_primary_file_matched"] is True
    assert any(item["strategy"] == "gpu-native-evidence" for item in payload["route_rationale"])
    assert any("-e" in call for call in calls)


def test_agent_capsule_gpu_evidence_reads_native_output_as_utf8(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def create_invoice():\n    return 'é'\n", encoding="utf-8")
    calls: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []

    def _fake_gpu_run(command, **kwargs):
        calls.append([str(part) for part in command])
        kwargs_seen.append(dict(kwargs))
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
            "total_matches": 0,
            "matches": [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    assert payload["gpu_acceleration"]["status"] == "ready_no_matches"
    gpu_kwargs = [
        kwargs
        for kwargs, command in zip(kwargs_seen, calls, strict=True)
        if "--gpu-device-ids" in [str(part) for part in command]
    ]
    assert gpu_kwargs
    assert all(kwargs["encoding"] == "utf-8" for kwargs in gpu_kwargs)
    assert all(kwargs["errors"] == "replace" for kwargs in gpu_kwargs)


def test_agent_capsule_gpu_evidence_payload_is_bounded(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target_path = project / "payments.py"
    target_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    calls = 0

    def _fake_gpu_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        matches = [
            {
                "file": str(target_path.resolve()),
                "line": index + 1,
                "text": f"def create_invoice_{index}():",
                "pattern_text": "invoice",
            }
            for index in range(12)
        ]
        payload = {
            "version": 1,
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
            "total_matches": len(matches),
            "total_files": 1,
            "requested_gpu_device_ids": [0],
            "routing_gpu_device_ids": [0],
            "pipeline": {"pattern_count": 1, "kernel_time_ms": 0.1},
            "matches": matches if calls > 1 else matches[:1],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "used"
    evidence_payload = acceleration["evidence"]["payload"]
    assert "matches" not in evidence_payload
    assert len(evidence_payload["matches_preview"]) == 3
    assert evidence_payload["matches_omitted"] == 9
    assert evidence_payload["total_matches"] == 12


def test_agent_capsule_gpu_probe_uses_resolved_native_tg(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )
    native_tg = tmp_path / "managed" / "tg.exe"
    native_tg.parent.mkdir()
    native_tg.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    assert calls
    assert calls[0][0] == str(native_tg)
    assert payload["gpu_acceleration"]["status"] == "unsupported"


def test_agent_capsule_gpu_evidence_rejects_sidecar_route(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "unsupported"
    assert acceleration["routing_backend"] == "GpuSidecar"
    assert acceleration["sidecar_used"] is True
    assert "sidecar-routed" in acceleration["reason"]
    assert len(calls) == 1


def test_agent_capsule_gpu_probe_summary_redacts_probe_paths(monkeypatch, tmp_path):
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
            "path": str(command[-1]),
            "total_matches": 1,
            "matches": [{"file": str(command[-1]) + "/probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    probe = payload["gpu_acceleration"]["probe"]
    serialized = json.dumps(probe)
    assert "tg-agent-gpu-probe" not in serialized
    assert "probe.log" not in serialized
    assert probe["payload"]["path"] == "<agent-gpu-probe-root>"
    assert probe["payload"]["matches_preview"][0]["file"] == "<agent-gpu-probe-file>"


def test_agent_capsule_gpu_probe_failure_redacts_probe_command_path(tmp_path):
    probe_root = tmp_path / "tg-agent-gpu-probe-secret"
    probe = agent_capsule._summarize_agent_gpu_json_result(
        {
            "status": "timeout",
            "command": f"tg search --json {probe_root}",
            "argv": ["tg", "search", "--json", str(probe_root)],
        },
        redact_probe_paths=True,
    )
    serialized = json.dumps(probe)

    assert "tg-agent-gpu-probe" not in serialized
    assert probe["argv"][-1] == "<agent-gpu-probe-root>"


def test_agent_capsule_cli_accepts_gpu_device_ids(monkeypatch, tmp_path):
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
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--gpu-device-ids",
            "0,1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["gpu_acceleration"]["requested_device_ids"] == [0, 1]
    assert payload["gpu_acceleration"]["status"] == "unsupported"


def test_agent_capsule_python_invoice_tax_query_selects_python_evidence(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "python"


def test_agent_capsule_python_invoice_tax_query_keeps_python_target_with_js_manifest(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "python"
    assert any("pytest" in command for command in payload["validation_commands"])
    assert payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_language_hint_beats_cross_language_lexical_noise(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["primary_target_language"] == "python"
    assert payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_file_name_hint_beats_cross_language_symbol_similarity(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  return subtotal + taxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "payments.py invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"


def test_agent_context_commands_prefer_invoice_implementation_over_service_mentions(tmp_path):
    paths = _write_invoice_service_ambiguity_fixture(tmp_path)

    context_result = CliRunner().invoke(
        app,
        [
            "context-render",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )
    edit_result = CliRunner().invoke(
        app,
        [
            "edit-plan",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )
    agent_payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert context_result.exit_code == 0, context_result.output
    context_payload = json.loads(context_result.stdout)
    assert context_payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())
    assert context_payload["navigation_pack"]["primary_target"]["file"] == str(
        paths["payments"].resolve()
    )
    assert edit_result.exit_code == 0, edit_result.output
    edit_payload = json.loads(edit_result.stdout)
    assert edit_payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())
    assert edit_payload["navigation_pack"]["primary_target"]["file"] == str(
        paths["payments"].resolve()
    )
    assert agent_payload["primary_target"]["file"] == str(paths["payments"].resolve())
    assert agent_payload["primary_target"]["symbol"] == "create_invoice"
    assert agent_payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_exact_symbol_query_prefers_exact_symbol_over_prefix(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "native_search.py"
    module_path.write_text(
        "def run_native_search_files():\n"
        "    total = 0\n"
        "    total += 1\n"
        "    total += 2\n"
        "    return total\n\n"
        "def run_native_search():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(project, "run_native_search")

    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "run_native_search"


def test_agent_capsule_filters_file_only_alternative_targets(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_path = src_dir / "payments.py"
    alternative_path = src_dir / "notes.py"
    primary_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    alternative_path.write_text("invoice notes\n", encoding="utf-8")

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(primary_path),
                    "symbol": "create_invoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                },
                "follow_up_reads": [],
                "validation_commands": [],
            },
            "edit_plan_seed": {
                "primary_file": str(primary_path),
                "primary_symbol": {"name": "create_invoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.9},
                "validation_plan": [],
                "validation_commands": [],
                "edit_ordering": [str(primary_path)],
            },
            "candidate_edit_targets": {
                "files": [str(alternative_path)],
                "symbols": [],
            },
            "file_matches": [
                {
                    "path": str(alternative_path),
                    "score": 80,
                    "reasons": ["source"],
                    "provenance": ["heuristic"],
                }
            ],
            "validation_commands": [],
            "sources": [
                {
                    "file": str(primary_path),
                    "symbol": "create_invoice",
                    "start_line": 1,
                    "end_line": 2,
                    "source": primary_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_context_render_from_map", _fake_context_render
    )

    payload = agent_capsule.build_agent_capsule(
        "create_invoice",
        project,
        include_blast_radius=False,
        max_tokens=400,
    )

    assert payload["alternative_targets"] == []


def test_context_pack_uses_repo_map_imports_for_direct_validation_evidence(
    monkeypatch, tmp_path: Path
):
    paths = _write_invoice_service_ambiguity_fixture(tmp_path)
    payload = repo_map.build_repo_map(paths["project"])

    def _fail_if_context_scoring_reparses_tests(*_args, **_kwargs):
        raise AssertionError("context scoring should reuse repo-map imports")

    monkeypatch.setattr(
        repo_map,
        "_file_imports_symbol_from_definition",
        _fail_if_context_scoring_reparses_tests,
    )

    context_payload = repo_map.build_context_pack_from_map(
        payload,
        "change invoice tax calculation",
    )

    assert context_payload["files"][0] == str(paths["payments"].resolve())
    primary_match = context_payload["file_matches"][0]
    assert primary_match["path"] == str(paths["payments"].resolve())
    assert "validation-direct-definition" in primary_match["reasons"]


def test_agent_capsule_change_invoice_tax_query_prefers_python_body_and_tests(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert any(
        command.startswith("uv run pytest tests/test_payments.py")
        for command in payload["validation_commands"]
    )
    assert payload["ask_user_before_editing"]["required"] is False
    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] == "tie_resolved"
    assert ambiguity["resolved_by"] == "targeted-validation"
    assert any(
        command.startswith("uv run pytest tests/test_payments.py")
        for command in ambiguity["resolution_evidence"]
    )
    assert ambiguity["requires_confirmation"] is False
    assert ambiguity["tie_count"] == 1
    assert ambiguity["tied_alternative_targets"][0]["file"] == str(paths["typescript"].resolve())


def test_agent_capsule_lsp_resolved_tie_reports_resolution_evidence(
    monkeypatch,
    tmp_path: Path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_path = src_dir / "payments.py"
    primary_path.write_text(
        "def create_invoice(subtotal):\n    return {'subtotal': subtotal, 'tax': subtotal * 0.1}\n",
        encoding="utf-8",
    )
    alternative_path = src_dir / "payments.ts"
    alternative_path.write_text(
        "export function createInvoice(subtotal: number): number {\n  return subtotal * 1.1;\n}\n",
        encoding="utf-8",
    )

    def _fake_context_render(*_args, **_kwargs):
        return {
            "routing_reason": "context-render",
            "navigation_pack": {
                "primary_target": {
                    "file": str(primary_path.resolve()),
                    "symbol": "create_invoice",
                    "kind": "function",
                    "line": 1,
                    "semantic_provider": "lsp",
                    "provenance": ["lsp-provider"],
                    "lsp_provider_response": True,
                    "lsp_proof": True,
                    "lsp_operation": "definition",
                    "lsp_resolution_basis": "textDocument/definition",
                },
                "validation_commands": [],
            },
            "edit_plan_seed": {
                "primary_file": str(primary_path.resolve()),
                "primary_symbol": {"name": "create_invoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.95},
            },
            "candidate_edit_targets": {
                "symbols": [
                    {
                        "file": str(alternative_path.resolve()),
                        "name": "createInvoice",
                        "kind": "function",
                        "line": 1,
                        "score": 12,
                    }
                ]
            },
            "file_matches": [
                {
                    "path": str(alternative_path.resolve()),
                    "score": 12,
                    "reasons": ["definition"],
                    "provenance": ["parser-backed"],
                }
            ],
            "sources": [
                {
                    "file": str(primary_path.resolve()),
                    "symbol": "create_invoice",
                    "start_line": 1,
                    "end_line": 2,
                    "source": primary_path.read_text(encoding="utf-8"),
                }
            ],
            "rendered_context": primary_path.read_text(encoding="utf-8"),
            "context_consistency": {
                "primary_file_included": True,
                "rendered_context_includes_primary": True,
            },
            "validation_plan": [],
            "validation_commands": [],
        }

    monkeypatch.setenv("TG_CAPSULE_LSP_CONFIDENCE_BOOST", "1")
    monkeypatch.setattr(
        agent_capsule.repo_map, "build_context_render_from_map", _fake_context_render
    )

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        include_blast_radius=False,
        max_tokens=400,
    )

    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] == "tie_resolved"
    assert ambiguity["resolved_by"] == "lsp"
    assert ambiguity["requires_confirmation"] is False
    assert ambiguity["resolution_evidence"]
    evidence = ambiguity["resolution_evidence"][0]
    assert evidence["kind"] == "lsp-primary-target-proof"
    assert evidence["file"] == str(primary_path.resolve())
    assert evidence["symbol"] == "create_invoice"
    assert evidence["semantic_provider"] == "lsp"
    assert evidence["lsp_proof"] is True
    assert evidence["lsp_provider_response"] is True
    assert evidence["lsp_operation"] == "definition"
    assert evidence["lsp_resolution_basis"] == "textDocument/definition"
    assert evidence["tied_alternative_count"] == 1
    assert evidence["tied_alternative_files"] == [str(alternative_path.resolve())]
    consistency = payload["context_consistency"]
    assert consistency["alternative_confidence_tie_resolved_by"] == "lsp"
    assert consistency["alternative_confidence_tie_resolution_evidence"] == [evidence]
    assert consistency["alternative_confidence_tie"] is False


def test_agent_capsule_ambiguous_invoice_tax_query_surfaces_cross_language_alternatives(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    alternatives = payload["alternative_targets"]
    assert any(
        item["file"] == str(paths["typescript"].resolve())
        and item["symbol"] == "createInvoice"
        and item["language"] == "typescript"
        for item in alternatives
    )
    assert all(item["file"] != payload["primary_target"]["file"] for item in alternatives)


def test_agent_capsule_alternative_confidence_does_not_exceed_selected_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert payload["alternative_targets"]
    primary_confidence = payload["primary_target"]["confidence"]
    assert all(item["confidence"] <= primary_confidence for item in payload["alternative_targets"])


def test_agent_capsule_equal_confidence_alternative_requires_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    python_path = src_dir / "payments.py"
    python_path.write_text(
        "def create_invoice(subtotal):\n    tax = subtotal * 0.0825\n    return subtotal + tax\n",
        encoding="utf-8",
    )
    typescript_path = src_dir / "app.ts"
    typescript_path.write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(python_path),
                    "symbol": "create_invoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                },
                "follow_up_reads": [],
                "validation_commands": [],
            },
            "edit_plan_seed": {
                "primary_file": str(python_path),
                "primary_symbol": {"name": "create_invoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 3},
                "confidence": {"overall": 0.9},
                "validation_plan": [],
                "validation_commands": [],
                "edit_ordering": [str(python_path)],
            },
            "candidate_edit_targets": {
                "symbols": [
                    {
                        "file": str(typescript_path),
                        "name": "createInvoice",
                        "kind": "function",
                        "line": 1,
                        "score": 90,
                    }
                ]
            },
            "file_matches": [
                {
                    "path": str(typescript_path),
                    "score": 90,
                    "reasons": ["source"],
                    "provenance": ["heuristic"],
                }
            ],
            "validation_commands": [],
            "sources": [
                {
                    "file": str(python_path),
                    "symbol": "create_invoice",
                    "start_line": 1,
                    "end_line": 3,
                    "source": python_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_context_render_from_map", _fake_context_render
    )

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        max_tokens=400,
    )

    assert payload["alternative_targets"]
    assert payload["context_consistency"]["alternative_confidence_tie"] is True
    assert payload["context_consistency"]["tied_alternative_targets"]
    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] == "tie_requires_confirmation"
    assert ambiguity["requires_confirmation"] is True
    assert ambiguity["tie_count"] == 1
    assert ambiguity["tied_alternative_targets"]
    assert payload["confidence"]["overall"] <= 0.74
    assert payload["primary_target"]["confidence"] <= 0.74
    assert payload["ask_user_before_editing"]["required"] is True
    assert (
        "alternative target confidence ties primary target"
        in payload["ask_user_before_editing"]["reasons"]
    )
