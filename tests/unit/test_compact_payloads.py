"""Tests for DF-14: `--compact` mode on blast-radius-plan, blast-radius-render, and context-render."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.compact_payloads import compact_payload_for_cli, dump_compact_json
from tensor_grep.cli.main import app


def _make_multifile_fixture(root: Path) -> Path:
    project = root / "billing"
    project.mkdir()
    (project / "core.py").write_text(
        '"""Core invoice calculation helpers with detailed documentation."""\n\n'
        "def compute_invoice(amount: float, tax_rate: float) -> float:\n"
        '    """Compute the final invoice total including tax and rounding."""\n'
        "    subtotal = round(amount, 2)\n"
        "    tax = round(subtotal * tax_rate, 2)\n"
        "    return round(subtotal + tax, 2)\n",
        encoding="utf-8",
    )
    for idx in range(1, 6):
        (project / f"service_{idx}.py").write_text(
            f"from core import compute_invoice\n\n"
            f"def bill_customer_{idx}(amount: float) -> float:\n"
            f'    """Service handler {idx} delegating to compute_invoice."""\n'
            f"    return compute_invoice(amount, 0.08)\n",
            encoding="utf-8",
        )
    (project / "test_billing.py").write_text(
        "from core import compute_invoice\n"
        "from service_1 import bill_customer_1\n\n"
        "def test_compute_invoice() -> None:\n"
        "    assert compute_invoice(100.0, 0.08) == 108.0\n\n"
        "def test_bill_customer_1() -> None:\n"
        "    assert bill_customer_1(100.0) == 108.0\n",
        encoding="utf-8",
    )
    return project


def test_context_render_compact_and_focused_stay_under_4000_bytes(tmp_path: Path) -> None:
    project = _make_multifile_fixture(tmp_path)
    runner = CliRunner()

    full_res = runner.invoke(
        app, ["context-render", str(project), "compute_invoice", "--max-files", "6", "--json"]
    )
    compact_res = runner.invoke(
        app,
        [
            "context-render",
            str(project),
            "compute_invoice",
            "--max-files",
            "6",
            "--json",
            "--compact",
        ],
    )
    assert full_res.exit_code == 0, full_res.output
    assert compact_res.exit_code == 0, compact_res.output

    raw_bytes = len(compact_res.stdout.strip().encode("utf-8"))
    assert raw_bytes <= 4000, f"context-render --compact emitted {raw_bytes} bytes (> 4000)"

    payload = json.loads(compact_res.stdout)
    assert payload["query"] == "compute_invoice"
    assert isinstance(payload["rendered_context"], str)
    assert payload["rendered_context"]
    assert "sections" in payload
    assert "context_consistency" in payload
    assert "omitted_sections" in payload
    assert "truncated" in payload
    for omitted in ("edit_plan_seed", "navigation_pack", "coverage", "file_summaries"):
        assert omitted not in payload

    focused_res = runner.invoke(
        app,
        [
            "context-render",
            str(project),
            "compute_invoice",
            "--max-files",
            "6",
            "--render-profile",
            "focused",
            "--json",
            "--compact",
        ],
    )
    assert focused_res.exit_code == 0, focused_res.output
    focused_bytes = len(focused_res.stdout.strip().encode("utf-8"))
    assert focused_bytes <= 4000, (
        f"context-render --render-profile focused --compact emitted {focused_bytes} bytes (> 4000)"
    )
    focused_payload = json.loads(focused_res.stdout)
    assert focused_payload["render_profile"] == "focused"
    assert "context_consistency" in focused_payload
    assert "omitted_sections" in focused_payload
    assert "rendered_context" in focused_payload


def test_blast_radius_render_and_plan_compact_stay_under_4000_bytes(tmp_path: Path) -> None:
    project = _make_multifile_fixture(tmp_path)
    runner = CliRunner()

    render_res = runner.invoke(
        app,
        [
            "blast-radius-render",
            str(project),
            "compute_invoice",
            "--max-files",
            "6",
            "--json",
            "--compact",
        ],
    )
    assert render_res.exit_code == 0, render_res.output
    render_bytes = len(render_res.stdout.strip().encode("utf-8"))
    assert render_bytes <= 4000, (
        f"blast-radius-render --compact emitted {render_bytes} bytes (> 4000)"
    )
    render_payload = json.loads(render_res.stdout)
    assert render_payload["symbol"] == "compute_invoice"
    assert render_payload["definitions"]
    assert isinstance(render_payload["rendered_context"], str)
    assert render_payload["rendered_context"]
    assert "omitted_sections" in render_payload
    assert "truncated" in render_payload
    for omitted in ("edit_plan_seed", "navigation_pack", "file_summaries", "rendered_caller_tree"):
        assert omitted not in render_payload

    plan_res = runner.invoke(
        app,
        [
            "blast-radius-plan",
            str(project),
            "compute_invoice",
            "--max-files",
            "6",
            "--json",
            "--compact",
        ],
    )
    assert plan_res.exit_code == 0, plan_res.output
    plan_bytes = len(plan_res.stdout.strip().encode("utf-8"))
    assert plan_bytes <= 4000, f"blast-radius-plan --compact emitted {plan_bytes} bytes (> 4000)"
    plan_payload = json.loads(plan_res.stdout)
    assert plan_payload["symbol"] == "compute_invoice"
    assert plan_payload["definitions"]
    assert plan_payload["files"]
    assert len(plan_payload["files"]) >= 2
    assert len(plan_payload["edit_order"]) >= 2
    assert plan_payload["tests"]
    assert "not_found" in plan_payload
    assert "result_incomplete" in plan_payload
    for omitted in ("edit_plan_seed", "navigation_pack", "file_summaries"):
        assert omitted not in plan_payload


def test_compact_rejects_missing_json_and_citations(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    runner = CliRunner()

    assert runner.invoke(app, ["context-render", str(tmp_path), "f", "--compact"]).exit_code == 2
    assert (
        runner.invoke(
            app, ["context-render", str(tmp_path), "f", "--json", "--compact", "--citations"]
        ).exit_code
        == 2
    )
    assert (
        runner.invoke(app, ["blast-radius-render", str(tmp_path), "f", "--compact"]).exit_code == 2
    )
    assert runner.invoke(app, ["blast-radius-plan", str(tmp_path), "f", "--compact"]).exit_code == 2


def test_compact_payload_does_not_mutate_and_omits_whole_objects_on_overflow() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "result_incomplete": True,
        "incomplete_reason": "deadline",
        "not_found": False,
        "symbol": "compute_invoice",
        "path": "billing",
        "context_consistency": {"validation_alignment": "aligned"},
        "omissions": {"omitted_section_count": 0},
        "definitions": [{"file": "core.py", "line": 3}],
        "files": ["core.py", "service_1.py"],
        "tests": ["test_billing.py"],
        "candidate_edit_targets": [
            {"file": f"service_{i}.py", "reason": "caller " + ("x" * 600)} for i in range(10)
        ],
        "edit_plan_seed": {"duplicated": "y" * 2000},
    }
    snapshot = copy.deepcopy(original)

    compacted = compact_payload_for_cli(original)
    assert original == snapshot
    assert "edit_plan_seed" not in compacted
    assert compacted["compact_truncated"] is True

    serialized = dump_compact_json(original)
    assert len(serialized.encode("utf-8")) <= 4000
    parsed = json.loads(serialized)
    assert parsed["compact_truncated"] is True
    assert parsed["result_incomplete"] is True
    assert parsed["incomplete_reason"] == "deadline"
    assert parsed["context_consistency"] == {"validation_alignment": "aligned"}
    # Remaining candidate_edit_targets entries must be intact (never sliced mid-object).
    for entry in parsed.get("candidate_edit_targets", []):
        assert set(entry.keys()) == {"file", "reason"}
        assert len(entry["reason"]) > 500


def test_compact_payload_deepcopy_does_not_mutate_nested_omitted_sections_or_omissions() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "query": "compute_invoice",
        "path": "billing",
        "omitted_sections": [
            {"section": "sources", "omitted_count": 2, "reason": "compact_budget"},
        ],
        "omissions": {
            "omitted_section_count": 1,
            "compact_omitted": {"coverage": 1},
        },
        "sources": [{"file": f"f_{i}.py", "body": "z" * 900} for i in range(8)],
        "coverage": {"covered": 1},
    }
    snapshot = copy.deepcopy(original)

    compacted = compact_payload_for_cli(original)
    assert original == snapshot
    assert compacted["omitted_sections"] != original["omitted_sections"]


def test_compact_payload_updates_context_consistency_and_drops_whole_sections_on_render_trim() -> (
    None
):
    sec_0 = "=== core.py ===\ndef compute_invoice():\n    return 1"
    sec_1 = "=== service_1.py ===\n" + ("x" * 2200)
    sec_2 = "=== service_2.py ===\ndef other_fn():\n    " + ("y" * 2200)
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "query": "compute_invoice",
        "path": "billing",
        "rendered_context": f"{sec_0}\n\n{sec_1}\n\n{sec_2}",
        "context_consistency": {
            "primary_file": "service_2.py",
            "primary_symbol": "other_fn",
            "primary_file_present": True,
            "primary_symbol_present": True,
            "rendered_context_includes_primary": True,
            "rendered_context_includes_primary_symbol": True,
        },
    }
    compacted = compact_payload_for_cli(original)
    assert len(dump_compact_json(original).encode("utf-8")) <= 4000
    assert sec_0 in compacted["rendered_context"]
    assert "service_2.py" not in compacted["rendered_context"]
    cc = compacted["context_consistency"]
    assert cc["truncated_by_compact"] is True
    assert cc["primary_file_present"] is False
    assert cc["primary_symbol_present"] is False
    assert cc["rendered_context_includes_primary"] is False
    assert cc["rendered_context_includes_primary_symbol"] is False


def test_compact_payload_extreme_fallback_preserves_safety_and_omissions_under_4000_bytes() -> None:
    original = {
        "version": "1.141.0",
        "schema_version": 2,
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "result_incomplete": True,
        "incomplete_reason": "deadline " + ("w" * 3000),
        "rollback": {"supported": True, "strategy": "git-checkout", "notes": "n" * 2000},
        "context_consistency": {
            "primary_file": "core.py",
            "primary_symbol": "compute_invoice",
            "validation_alignment": {"extra": "v" * 2000},
        },
        "omitted_sections": [
            {"section": f"sec_{i}", "omitted_count": i, "reason": "compact_budget"}
            for i in range(60)
        ],
        "omissions": {"omitted_section_count": 3, "compact_omitted": {"big": "b" * 1500}},
        "suggestions": [f"suggestion-{i}-" + ("s" * 200) for i in range(20)],
        "definitions": [{"file": "core.py", "line": 3, "body": "d" * 4500}],
    }
    serialized = dump_compact_json(original)
    assert len(serialized.encode("utf-8")) <= 4000
    parsed = json.loads(serialized)
    for required_key in (
        "compact_truncated",
        "omissions",
        "omitted_sections",
        "result_incomplete",
        "incomplete_reason",
        "context_consistency",
        "rollback",
        "version",
        "schema_version",
        "routing_backend",
        "routing_reason",
    ):
        assert required_key in parsed, f"Missing essential fallback key: {required_key}"
    assert parsed["compact_truncated"] is True
    assert parsed["result_incomplete"] is True


def test_compact_payload_records_exact_dropped_list_count_and_diagnostic_omissions() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "symbol": "compute_invoice",
        "path": "billing",
        "coverage": {"total": 10},
        "file_summaries": [{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}],
        "definitions": [{"file": "core.py", "line": 3, "body": "d" * 4200} for _ in range(4)],
    }
    compacted = compact_payload_for_cli(original)
    assert compacted["compact_truncated"] is True
    compact_omitted = compacted["omissions"]["compact_omitted"]
    assert compact_omitted["coverage"] == 1
    assert compact_omitted["file_summaries"] == 3
    assert compact_omitted["definitions"] == 4
    assert compacted["omissions"]["omitted_section_count"] == 3
    assert compacted["omissions"]["omitted_count"] == 8


def test_compact_payload_extreme_fallback_preserves_ask_user_confidence_and_truncated() -> None:
    original = {
        "version": "1.141.0",
        "schema_version": 2,
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "truncated": True,
        "confidence": {
            "overall": 0.45,
            "level": "low",
            "downgrade_reasons": [f"reason-{i}-" + ("r" * 200) for i in range(15)],
        },
        "ask_user_before_editing": {
            "required": True,
            "reasons": ["low confidence target " + ("x" * 1500), "extra reason " + ("y" * 1500)],
            "extra": "z" * 1500,
        },
        "incomplete_reason": "deadline " + ("w" * 3000),
        "omitted_sections": [
            {"section": f"sec_{i}", "omitted_count": 2, "reason": "compact_budget"}
            for i in range(50)
        ],
        "definitions": [{"file": "core.py", "line": 3, "body": "d" * 4500}],
    }
    serialized = dump_compact_json(original)
    assert len(serialized.encode("utf-8")) <= 4000
    parsed = json.loads(serialized)
    assert parsed["compact_truncated"] is True
    assert parsed["truncated"] is True
    assert parsed["ask_user_before_editing"]["required"] is True
    assert "confidence" in parsed
    assert parsed["omissions"]["omitted_section_count"] >= 1
    assert parsed["omissions"]["omitted_count"] >= 1


def test_compact_payload_omitted_section_count_vs_omitted_count_distinct_sections() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "symbol": "compute_invoice",
        "path": "billing",
        "omissions": {
            "omitted_section_count": 1,
            "omitted_count": 2,
            "compact_omitted": {"coverage": 2},
        },
        "coverage": {"more": 1},
        "file_summaries": [{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}],
        "definitions": [{"file": "core.py", "line": 3, "body": "d" * 4200} for _ in range(4)],
    }
    compacted = compact_payload_for_cli(original)
    omissions = compacted["omissions"]
    # coverage already existed (still 1 section, +1 item -> 3), plus file_summaries (+1 section, +3),
    # plus definitions (+1 section, +4) => 3 distinct sections, 10 total omitted items.
    assert omissions["omitted_section_count"] == 3
    assert omissions["omitted_count"] == 10


def test_compact_payload_downgrades_confidence_when_render_trim_drops_primary() -> None:
    sec_0 = "=== core.py ===\ndef compute_invoice():\n    return 1"
    sec_1 = "=== service_1.py ===\n" + ("x" * 2200)
    sec_2 = "=== service_2.py ===\ndef other_fn():\n    " + ("y" * 2200)
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "query": "compute_invoice",
        "path": "billing",
        "rendered_context": f"{sec_0}\n\n{sec_1}\n\n{sec_2}",
        "confidence": {"overall": 0.9, "level": "high", "band": "high", "downgrade_reasons": []},
        "context_consistency": {
            "primary_file": "service_2.py",
            "primary_symbol": "other_fn",
            "primary_file_present": True,
            "primary_symbol_present": True,
        },
    }
    compacted = compact_payload_for_cli(original)
    assert compacted["context_consistency"]["confidence_downgraded"] is True
    assert compacted["confidence"]["compact_confidence_downgraded"] is True
    assert compacted["confidence"]["overall"] <= 0.55
    assert compacted["confidence"]["level"] == "low"
    assert compacted["confidence"]["band"] == "low"
    assert "primary context omitted by compact" in compacted["confidence"]["downgrade_reasons"]


def test_compact_payload_downgrades_medium_confidence_to_low_when_both_primary_missing() -> None:
    sec_0 = "=== core.py ===\ndef compute_invoice():\n    return 1"
    sec_1 = "=== service_1.py ===\n" + ("x" * 2200)
    sec_2 = "=== service_2.py ===\ndef other_fn():\n    " + ("y" * 2200)
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "query": "compute_invoice",
        "path": "billing",
        "rendered_context": f"{sec_0}\n\n{sec_1}\n\n{sec_2}",
        "confidence": {"overall": 0.65, "level": "medium", "band": "medium"},
        "context_consistency": {
            "primary_file": "service_2.py",
            "primary_symbol": "other_fn",
            "primary_file_present": True,
            "primary_symbol_present": True,
        },
    }
    compacted = compact_payload_for_cli(original)
    assert compacted["context_consistency"]["primary_file_present"] is False
    assert compacted["context_consistency"]["primary_symbol_present"] is False
    assert compacted["confidence"]["level"] == "low"
    assert compacted["confidence"]["band"] == "low"


def test_compact_payload_extreme_fallback_updates_existing_smaller_omissions_counters() -> None:
    original = {
        "version": "1.141.0",
        "schema_version": 2,
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "omitted_sections": [
            {"section": f"sec_{i}", "omitted_count": 2, "reason": "compact_budget"}
            for i in range(60)
        ],
        "omissions": {"omitted_section_count": 1, "omitted_count": 1},
        "definitions": [{"file": "core.py", "line": 3, "body": "d" * 4500}],
    }
    serialized = dump_compact_json(original)
    assert len(serialized.encode("utf-8")) <= 4000
    parsed = json.loads(serialized)
    popped_sections = 60 - len(parsed["omitted_sections"])
    assert popped_sections > 1
    assert parsed["omissions"]["omitted_section_count"] >= popped_sections
    assert parsed["omissions"]["omitted_count"] >= popped_sections * 2


def test_compact_payload_sheds_candidate_edit_targets_before_trimming_edit_order() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "symbol": "compute_invoice",
        "path": "billing",
        "definitions": [{"file": "core.py", "line": 3}],
        "files": ["core.py", "service_1.py", "service_2.py"],
        "tests": ["test_billing.py"],
        "edit_order": [
            {"file": "core.py", "role": "definition"},
            {"file": "service_1.py", "role": "caller"},
            {"file": "service_2.py", "role": "caller"},
        ],
        "candidate_edit_targets": {
            "files": ["core.py", "service_1.py", "service_2.py"],
            "spans": [{"file": f"service_{i}.py", "blob": "x" * 600} for i in range(6)],
        },
        "navigation_pack": {"blob": "n" * 2000},
    }
    compacted = compact_payload_for_cli(original)
    assert len(json.dumps(compacted, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) <= 4000
    assert "navigation_pack" not in compacted
    assert "candidate_edit_targets" not in compacted
    assert len(compacted["edit_order"]) == 3
    assert compacted["files"] == ["core.py", "service_1.py", "service_2.py"]
    assert compacted["tests"] == ["test_billing.py"]


def test_compact_payload_keeps_at_least_one_edit_order_under_list_trim() -> None:
    original = {
        "routing_backend": "RepoMap",
        "routing_reason": "symbol-blast-radius-plan",
        "symbol": "compute_invoice",
        "path": "billing",
        "definitions": [{"file": "core.py", "line": 3}],
        "files": ["core.py"],
        "tests": ["test_billing.py"],
        "edit_order": [
            {"file": f"service_{i}.py", "reason": "r" * 400} for i in range(15)
        ],
    }
    compacted = compact_payload_for_cli(original)
    assert len(json.dumps(compacted, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) <= 4000
    assert len(compacted["edit_order"]) >= 1
