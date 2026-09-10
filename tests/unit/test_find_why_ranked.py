from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_find_why_ranked_and_install_state_in_json(tmp_path: Path) -> None:
    f1 = tmp_path / "refusal.py"
    f1.write_text("def _emit_broad_scan_refusal():\n    pass\n", encoding="utf-8")

    res = runner.invoke(
        app, ["find", "broad_scan_refusal", str(tmp_path), "--json", "--why-ranked"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert "install_state" in payload
    assert "dense_ready" in payload["install_state"] or "bm25_only" in payload["install_state"]
    assert len(payload["matches"]) > 0
    first_match = payload["matches"][0]
    assert "why_ranked" in first_match
    assert isinstance(first_match["why_ranked"], list)


def test_why_ranked_does_not_alter_match_order(tmp_path: Path) -> None:
    """Regression pin (AGT-08 Task 11): `--why-ranked` is a pure additive annotation.

    Runs the identical query against the identical fixture with `--why-ranked` omitted
    and then passed, and asserts the returned match order (by file path) is byte-identical
    both times. This freezes CURRENT behavior before any future contribution-recording work
    touches the ranking pipeline, so a later refactor can prove it didn't silently reorder
    results just because an explanation was requested alongside them.
    """
    (tmp_path / "refusal.py").write_text(
        "def _emit_broad_scan_refusal():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "other_refusal.py").write_text(
        "def another_broad_scan_refusal_helper():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "unrelated.py").write_text(
        "def broad_scan_refusal_note():\n    # broad_scan_refusal mentioned in a comment\n"
        "    pass\n",
        encoding="utf-8",
    )

    res_without = runner.invoke(app, ["find", "broad_scan_refusal", str(tmp_path), "--json"])
    assert res_without.exit_code == 0
    payload_without = json.loads(res_without.stdout)
    order_without = [m["file"] for m in payload_without["matches"]]

    res_with = runner.invoke(
        app, ["find", "broad_scan_refusal", str(tmp_path), "--json", "--why-ranked"]
    )
    assert res_with.exit_code == 0
    payload_with = json.loads(res_with.stdout)
    order_with = [m["file"] for m in payload_with["matches"]]

    assert len(order_without) > 1, "fixture must produce multiple matches to detect reordering"
    assert order_with == order_without, (
        "--why-ranked must never alter returned match order (pure additive annotation); "
        f"without={order_without!r} with={order_with!r}"
    )
