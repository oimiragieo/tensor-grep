import json
import re
import tomllib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]

UNIFIED_DESCRIPTION = (
    "Fast local code search, symbol navigation, and token-budgeted AI context "
    "(Rust + tree-sitter + MCP; zero API key required, GPU experimental)"
)


def test_packaging_manifests_share_unified_positioning_and_license() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert project["readme"] == "README.md"
    assert project["description"] == UNIFIED_DESCRIPTION

    winget_raw = (_REPO_ROOT / "scripts" / "oimiragieo.tensor-grep.yaml").read_text(
        encoding="utf-8"
    )
    winget = yaml.safe_load(winget_raw)
    assert winget["License"] == "Apache-2.0"
    assert winget["ShortDescription"] == UNIFIED_DESCRIPTION
    assert winget["Description"] == UNIFIED_DESCRIPTION

    npm_pkg = json.loads((_REPO_ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert npm_pkg["license"] == "Apache-2.0"
    assert npm_pkg["description"] == UNIFIED_DESCRIPTION

    brew_text = (_REPO_ROOT / "scripts" / "tensor-grep.rb").read_text(encoding="utf-8")
    desc_match = re.search(r'^\s*desc\s+"([^"]+)"', brew_text, re.MULTILINE)
    assert desc_match is not None
    assert len(desc_match.group(1)) <= 80
    assert "code search" in desc_match.group(1).lower()
    assert "cybert" not in desc_match.group(1).lower()

    for label, text in (
        ("pyproject.toml", project["description"]),
        ("winget", winget_raw),
        ("npm/package.json", json.dumps(npm_pkg)),
        ("scripts/tensor-grep.rb", brew_text),
    ):
        assert "cyBERT" not in text, label
        assert "Log Parsing" not in text, label
