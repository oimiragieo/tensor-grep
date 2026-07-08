"""Tests for build_orient_capsule -- the one-call codebase orientation capsule."""

import json
from pathlib import Path

from tensor_grep.cli.orient_capsule import build_orient_capsule, build_orient_capsule_json


def test_central_files_ranked_by_graph_score(tmp_path: Path) -> None:
    # hub.py is imported by two files; leaf/other import it -> hub is most central.
    (tmp_path / "hub.py").write_text("def hub_fn():\n    pass\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text(
        "import hub\n\n\ndef leaf_fn():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "other.py").write_text(
        "import hub\n\n\ndef other_fn():\n    pass\n", encoding="utf-8"
    )

    payload = build_orient_capsule(
        tmp_path, max_central_files=5, max_snippet_files=1, max_tokens=500
    )

    central = payload["central_files"]
    assert len(central) >= 1
    assert central[0]["file"].endswith("hub.py")
    assert "graph_score" in central[0]
    assert central[0]["graph_score"] > 0.0


def test_capsule_has_required_keys_and_routing(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    payload = build_orient_capsule(tmp_path, max_tokens=500)
    for key in (
        "path",
        "central_files",
        "entry_points",
        "symbol_map",
        "snippets",
        "token_estimate",
        "token_budget_label",
        "truncated",
        "scan_limit",
        "routing_reason",
    ):
        assert key in payload, f"missing capsule key: {key}"
    assert payload["routing_reason"] == "orient"
    assert isinstance(payload["token_estimate"], int)


def test_entry_points_detected_by_name(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    payload = build_orient_capsule(tmp_path, max_tokens=500)
    entry_files = [e["file"] for e in payload["entry_points"]]
    assert any(f.endswith("main.py") for f in entry_files)
    assert not any(f.endswith("helper.py") for f in entry_files)


def test_token_budget_respected(tmp_path: Path) -> None:
    # A big file should not blow the snippet token budget.
    big = "\n".join(f"def fn_{i}():\n    return {i}" for i in range(200))
    (tmp_path / "big.py").write_text(big, encoding="utf-8")
    payload = build_orient_capsule(
        tmp_path, max_central_files=3, max_snippet_files=3, max_tokens=120
    )
    snippet_tokens = sum(len(s["source"]) / 3.5 for s in payload["snippets"])
    assert snippet_tokens <= 120 + 50  # within budget (+ slack for the final truncated chunk)


def test_json_output_is_parseable(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    text = build_orient_capsule_json(tmp_path, max_tokens=500)
    parsed = json.loads(text)
    assert parsed["routing_reason"] == "orient"


def test_truncated_false_when_final_snippet_lands_exactly_on_budget(tmp_path, monkeypatch):
    """A snippet that fits EXACTLY on the token budget (nothing dropped, the loop completes) must
    not be flagged truncated. The old `token_budget_used >= max_tokens` proxy false-flagged this."""
    import tensor_grep.cli.orient_capsule as oc

    (tmp_path / "hub.py").write_text("def hub_fn():\n    return 1\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text(
        "import hub\n\n\ndef leaf_fn():\n    return hub.hub_fn()\n", encoding="utf-8"
    )
    monkeypatch.setattr(oc._repo_map, "_estimate_tokens", lambda *_a, **_k: 100)

    # One snippet, budget == its exact token estimate -> fits whole, loop completes, nothing cut.
    payload = build_orient_capsule(tmp_path, max_snippet_files=1, max_tokens=100)
    assert payload["snippets"], "expected at least one snippet"
    assert all(not s["truncated"] for s in payload["snippets"])
    assert payload["truncated"] is False


def test_truncated_true_when_budget_drops_a_later_snippet(tmp_path, monkeypatch):
    """When the budget cannot fit a further central file's snippet, content IS dropped -> truncated
    stays True (the fix must not lose this real-truncation signal)."""
    import tensor_grep.cli.orient_capsule as oc

    (tmp_path / "hub.py").write_text("def hub_fn():\n    return 1\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text(
        "import hub\n\n\ndef leaf_fn():\n    return hub.hub_fn()\n", encoding="utf-8"
    )
    (tmp_path / "other.py").write_text(
        "import hub\n\n\ndef other_fn():\n    return hub.hub_fn()\n", encoding="utf-8"
    )
    monkeypatch.setattr(oc._repo_map, "_estimate_tokens", lambda *_a, **_k: 100)

    # Two snippet slots but the budget (100) fits only the first -> the second is dropped.
    payload = build_orient_capsule(tmp_path, max_snippet_files=2, max_tokens=100)
    assert payload["truncated"] is True
