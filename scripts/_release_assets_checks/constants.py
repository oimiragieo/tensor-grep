"""Shared constants for the validate_release_assets check modules.

Pure data -- never monkeypatched by tests (verified via AST scan of
tests/unit/test_release_assets_validation_*.py: only ``_read`` and the four
``_version_from_*`` primitives are ever reassigned on the loaded module).
Safe to import by value into every sibling module and into the
``validate_release_assets.py`` facade.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINGET_SINGLETON_SCHEMA_HEADER = (
    "# yaml-language-server: $schema=https://aka.ms/winget-manifest.singleton.1.12.0.schema.json"
)
RELEASE_DOC_PATHS = (
    "AGENTS.md",
    "README.md",
    "SKILL.md",
    "docs/SESSION_HANDOFF.md",
    "docs/CONTINUATION_PLAN.md",
    "docs/CONTRACTS.md",
)
# The full set of blocking gates the `release` (Semantic Release) job must depend on. Spot-checking
# only `benchmark-regression` let a dropped gate (e.g. `static-analysis`, `windows-agent-readiness`)
# pass validation while still publishing without that check having run.
RELEASE_JOB_REQUIRED_GATES = (
    "smoke",
    "release-readiness",
    "agent-readiness",
    "windows-agent-readiness",
    "package-manager-readiness",
    "static-analysis",
    "test-python",
    "test-rust-core",
    "search-golden-parity",
    "native-build-smoke",
    "test-gpu-linux",
    "benchmark-regression",
)

_AST_GREP_VERSION_PIN_RE = re.compile(r"cargo install ast-grep --version ([0-9][0-9A-Za-z.\-]*)")
