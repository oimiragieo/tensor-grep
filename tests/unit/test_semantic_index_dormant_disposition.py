"""Guards the explicit retain/adopt/deprecate decision recorded in
docs/design/2026-09-07-cache-ownership.md (P9 extension, Task 06 checkbox 2).

`semantic_index.py` is deliberately unwired scaffolding, RETAINED as library-only per that
design doc. This test fails closed on silent drift in either direction:
- the module's honesty docstring disappearing without the design doc being updated, or
- a production module gaining an import of it without the design doc's ownership table
  being updated to reflect the new caller.

An in-repo "no callers" census is not itself deletion authority (per the plan's own Task 06
wording) -- this test only pins the CURRENT documented state, not a claim that the module
must stay dormant forever.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_INDEX_PATH = REPO_ROOT / "src" / "tensor_grep" / "core" / "semantic_index.py"
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "design" / "2026-09-07-cache-ownership.md"

# The exact production modules this design doc's ownership table currently names as
# non-callers. If one of these starts importing semantic_index, the doc's disposition
# ("RETAIN, library-only, not adopted") is stale and must be updated deliberately, not
# silently outrun by a new caller.
_KNOWN_NON_CALLER_PRODUCTION_MODULES = (
    "src/tensor_grep/cli/prepare_service.py",
    "src/tensor_grep/cli/main.py",
    "src/tensor_grep/cli/mcp_server.py",
    "src/tensor_grep/cli/session_store.py",
    "src/tensor_grep/cli/session_resume_service.py",
    "src/tensor_grep/cli/session_daemon.py",
)


def test_design_doc_exists_and_records_retain_decision() -> None:
    assert DESIGN_DOC_PATH.exists(), (
        "docs/design/2026-09-07-cache-ownership.md is missing -- the P9 extension's "
        "retain/adopt/deprecate decision for semantic_index.py must be recorded there."
    )
    text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    assert "RETAIN, library-only, not adopted" in text, (
        "The design doc's explicit disposition heading has changed or gone missing -- "
        "if the decision changed, update this test's expectation deliberately."
    )


def test_semantic_index_still_declares_itself_unwired() -> None:
    """If this docstring's honesty language changes, the design doc's premise is stale."""
    text = SEMANTIC_INDEX_PATH.read_text(encoding="utf-8")
    assert "NOT yet wired into the CLI" in text, (
        "semantic_index.py's module docstring no longer says it is unwired -- either the "
        "module was wired up (update docs/design/2026-09-07-cache-ownership.md's disposition "
        "from RETAIN to ADOPT) or the docstring wording changed independently of this test."
    )


def _module_imports_semantic_index(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "semantic_index" in node.module:
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "semantic_index" in alias.name:
                    return True
    return False


def test_known_non_caller_production_modules_still_do_not_import_semantic_index() -> None:
    """Pins the design doc's ownership table: these production modules are documented as
    NOT importing semantic_index.py today. A new import in any of them means the module is
    being adopted -- update the design doc's decision instead of letting this drift silently.
    """
    newly_importing = [
        rel
        for rel in _KNOWN_NON_CALLER_PRODUCTION_MODULES
        if (REPO_ROOT / rel).exists() and _module_imports_semantic_index(REPO_ROOT / rel)
    ]
    assert not newly_importing, (
        f"{newly_importing} now import semantic_index.py, but "
        "docs/design/2026-09-07-cache-ownership.md still says RETAIN/library-only/no "
        "production callers. Update the design doc's disposition to ADOPT (or partial-adopt) "
        "and its ownership table before this test can pass."
    )
