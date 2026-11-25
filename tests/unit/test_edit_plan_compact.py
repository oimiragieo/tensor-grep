from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.detail_tier import compact_payload
from tensor_grep.cli.main import app


def _make_three_file_fixture(root: Path) -> Path:
    project = root / "shop"
    project.mkdir()
    (project / "pricing.py").write_text(
        "def apply_discount(price: float, pct: float) -> float:\n    return price * (1.0 - pct)\n",
        encoding="utf-8",
    )
    (project / "cart.py").write_text(
        "from pricing import apply_discount\n\n"
        "def checkout(total: float) -> float:\n"
        "    return apply_discount(total, 0.1)\n",
        encoding="utf-8",
    )
    (project / "test_cart.py").write_text(
        "from cart import checkout\n\n"
        "def test_checkout() -> None:\n"
        "    assert checkout(100.0) == 90.0\n",
        encoding="utf-8",
    )
    return project


def test_edit_plan_compact_emits_under_4000_bytes_without_duplicated_blocks(tmp_path: Path) -> None:
    project = _make_three_file_fixture(tmp_path)
    runner = CliRunner()

    full_res = runner.invoke(app, ["edit-plan", str(project), "add discount", "--json"])
    compact_res = runner.invoke(
        app, ["edit-plan", str(project), "add discount", "--json", "--compact"]
    )
    assert full_res.exit_code == 0, full_res.output
    assert compact_res.exit_code == 0, compact_res.output

    raw_bytes = len(compact_res.stdout.strip().encode("utf-8"))
    assert raw_bytes < 4000, f"edit-plan --compact emitted {raw_bytes} bytes (>= 4000)"

    payload = json.loads(compact_res.stdout)
    assert payload["primary_target"]["file"].endswith("pricing.py")
    assert payload["files"]
    assert payload["validation_commands"]
    for omitted in (
        "edit_plan_seed",
        "navigation_pack",
        "candidate_edit_targets",
        "plan",
        "symbol_cache",
        "coverage_summary",
        "graph_trust_summary",
    ):
        assert omitted not in payload


def test_orient_and_agent_compact_emit_under_4000_bytes(tmp_path: Path) -> None:
    project = _make_three_file_fixture(tmp_path)
    runner = CliRunner()

    orient_res = runner.invoke(app, ["orient", str(project), "--json", "--compact"])
    assert orient_res.exit_code == 0, orient_res.output
    orient_bytes = len(orient_res.stdout.strip().encode("utf-8"))
    assert orient_bytes < 4000, f"orient --compact emitted {orient_bytes} bytes (>= 4000)"
    orient_payload = json.loads(orient_res.stdout)
    assert "central_files" in orient_payload
    assert "symbol_cache" not in orient_payload

    agent_res = runner.invoke(app, ["agent", str(project), "add discount", "--json", "--compact"])
    assert agent_res.exit_code == 0, agent_res.output
    agent_bytes = len(agent_res.stdout.strip().encode("utf-8"))
    assert agent_bytes < 4000, f"agent --compact emitted {agent_bytes} bytes (>= 4000)"
    agent_payload = json.loads(agent_res.stdout)
    assert agent_payload["primary_target"]["file"].endswith("pricing.py")
    for preserved in ("context_consistency", "omissions", "rollback"):
        assert preserved in agent_payload
    for omitted in ("raw_context_ref", "symbol_cache", "gpu_acceleration"):
        assert omitted not in agent_payload


def test_compact_preserves_capsule_safety_and_bounds_oversized_snippets() -> None:
    from tensor_grep.cli.detail_tier import dump_detail_json

    payload = {
        "context_consistency": {"validation_alignment": "mismatch"},
        "omissions": {"omitted_section_count": 2},
        "rollback": {"command": "tg checkpoint create ."},
        "snippets": [{"text": "x" * 4100}],
    }
    serialized = dump_detail_json(payload, "compact")
    assert len(serialized.encode("utf-8")) <= 4000
    parsed = json.loads(serialized)
    assert parsed["context_consistency"] == {"validation_alignment": "mismatch"}
    assert parsed["omissions"] == {"omitted_section_count": 2}
    assert parsed["rollback"] == {"command": "tg checkpoint create ."}
    assert parsed["compact_truncated"] is True


def test_agent_compact_rejects_conflicting_detail_or_missing_json(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    runner = CliRunner()
    res_conflict = runner.invoke(
        app, ["agent", str(tmp_path), "f", "--json", "--compact", "--detail", "full"]
    )
    assert res_conflict.exit_code == 2
    res_no_json = runner.invoke(app, ["agent", str(tmp_path), "f", "--compact"])
    assert res_no_json.exit_code == 2
    assert runner.invoke(app, ["orient", str(tmp_path), "--compact"]).exit_code == 2
    assert runner.invoke(app, ["edit-plan", str(tmp_path), "f", "--compact"]).exit_code == 2

    from tensor_grep.cli.detail_tier import dump_detail_json

    large_evidence = {
        "context_consistency": {"validation_alignment": "aligned"},
        "omissions": {"omitted_section_count": 0},
        "rollback": {"command": "tg checkpoint create ."},
        "call_site_evidence": [{"snippet": "y" * 5000, "file": "a.py"}],
    }
    bounded = dump_detail_json(large_evidence, "compact")
    assert len(bounded.encode("utf-8")) <= 4000
    assert "call_site_evidence" in json.loads(bounded)


def test_compact_payload_does_not_mutate_input() -> None:
    original = {
        "primary_target": {"file": "a.py"},
        "edit_plan_seed": {"primary_file": "a.py"},
        "navigation_pack": {"primary_target": {"file": "a.py"}},
        "candidate_edit_targets": {"files": ["a.py"]},
        "plan": {"primary_file": "a.py"},
        "resolution_gaps": [{"file": f"f{i}.py"} for i in range(12)],
    }
    snapshot = dict(original)
    compacted = compact_payload(original)
    assert original == snapshot
    assert compacted["primary_target"] == {"file": "a.py"}
    assert "edit_plan_seed" not in compacted
    assert "navigation_pack" not in compacted
    assert "candidate_edit_targets" not in compacted
    assert "plan" not in compacted
    assert compacted["resolution_gaps_truncated_count"] == 2
