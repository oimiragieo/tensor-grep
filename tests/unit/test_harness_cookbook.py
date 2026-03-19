from pathlib import Path

COOKBOOK_PATH = Path("docs/harness_cookbook.md")
README_PATH = Path("README.md")


def test_harness_cookbook_covers_public_workflows() -> None:
    doc = COOKBOOK_PATH.read_text(encoding="utf-8")

    assert "# Harness Cookbook" in doc
    assert "## Search JSON Flow" in doc
    assert "## Indexed Search Flow" in doc
    assert "## Rewrite Planning Flow" in doc
    assert "## Diff Review Flow" in doc
    assert "## Apply + Verify Flow" in doc
    assert "## NDJSON Streaming Flow" in doc
    assert "## MCP Workflow Flow" in doc
    assert "## Calibrate and Routing Flow" in doc
    assert "## Large Corpus Guidance" in doc
    assert "tg.exe search --json" in doc
    assert "tg.exe search --index --json" in doc
    assert "tg.exe search --ndjson" in doc
    assert "tg.exe run --lang python --rewrite" in doc
    assert "--diff" in doc
    assert "--apply --verify --json" in doc
    assert "tg.exe calibrate" in doc
    assert '"routing_backend"' in doc
    assert '"routing_reason"' in doc
    assert '"sidecar_used"' in doc
    assert "tg_rewrite_plan" in doc
    assert "tg_rewrite_apply" in doc
    assert "tg_rewrite_diff" in doc
    assert "tg_index_search" in doc


def test_readme_points_harness_consumers_to_contract_and_cookbook_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/harness_api.md" in readme
    assert "docs/harness_cookbook.md" in readme
