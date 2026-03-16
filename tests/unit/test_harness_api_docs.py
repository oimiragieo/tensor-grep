import json
from pathlib import Path

DOC_PATH = Path("docs/harness_api.md")
EXAMPLES_DIR = Path("docs/examples")
EXPECTED_EXAMPLES = {
    "search.json": ("total_matches", "matches"),
    "index_search.json": ("sidecar_used", "matches"),
    "rewrite_plan.json": ("total_edits", "edits"),
    "rewrite_apply_verify.json": ("plan", "verification"),
    "gpu_sidecar_search.json": ("sidecar_used", "matches"),
}


def test_harness_api_doc_covers_all_required_json_shapes() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "# Harness API" in doc
    assert "## Search JSON" in doc
    assert "## Index Search JSON" in doc
    assert "## Rewrite Plan JSON" in doc
    assert "## Apply + Verify JSON" in doc
    assert "## GPU Sidecar JSON" in doc
    assert "## Diff Output" in doc
    assert "routing_backend" in doc
    assert "routing_reason" in doc
    assert "version" in doc
    assert "line_number" in doc
    assert "line" in doc
    assert "---" in doc
    assert "+++" in doc
    assert "@@" in doc


def test_harness_api_examples_exist_and_have_unified_envelope() -> None:
    assert EXAMPLES_DIR.is_dir()

    for file_name, required_keys in EXPECTED_EXAMPLES.items():
        payload = json.loads((EXAMPLES_DIR / file_name).read_text(encoding="utf-8"))

        assert isinstance(payload["version"], int)
        assert isinstance(payload["routing_backend"], str)
        assert payload["routing_backend"]
        assert isinstance(payload["routing_reason"], str)
        assert payload["routing_reason"]

        for key in required_keys:
            assert key in payload


def test_harness_api_examples_are_non_trivial_single_document_json() -> None:
    example_paths = sorted(EXAMPLES_DIR.glob("*.json"))

    assert len(example_paths) >= 5

    for path in example_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total_matches = payload.get("total_matches")
        total_edits = payload.get("total_edits")
        nested_total_edits = payload.get("plan", {}).get("total_edits")

        assert total_matches or total_edits or nested_total_edits, (
            f"{path.name} should include matches or edits"
        )
