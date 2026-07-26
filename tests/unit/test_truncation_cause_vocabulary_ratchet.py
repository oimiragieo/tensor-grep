"""A RATCHET over `scan_limit.truncation_cause` -- a closed vocabulary agents parse.

Task #293. `tg inventory --json` and `tg docs-coverage --json` both expose
`scan_limit.truncation_cause`, and an agent branches on its value to decide whether to retry with
a bigger budget or to give up and tell a human. A new cause value appearing without documentation
means an agent hits a string it cannot interpret -- and the fail-closed rule (AGENTS.md) says an
uninterpretable cause must never default to "raise the limit", so an undocumented value silently
degrades every consumer.

WHAT THIS IS *NOT*. `truncation_cause` is a DIFFERENT FIELD from `incomplete_reason_class` /
`partial_reason`, which the search and codemap routes carry. Those use an underscored vocabulary
("scan_limit", "deadline", "timeout", "unreadable_path") and docs/CONTRACTS.md documents it in
five places. `truncation_cause` uses a hyphenated one ("project-files", "deadline",
"unreadable-path"). MEASURED: CONTRACTS.md mentions `unreadable_path` 5x and `unreadable-path` 0x.

The two spellings are NOT a bug to be unified. Each is internally consistent within its own field,
and renaming either would break a documented contract for zero correctness gain. An earlier draft
of #293 proposed exactly that rename on the strength of seeing two call sites; reading all ten
showed the real gap is that `truncation_cause` was never written down. THIS TEST IS THAT
DOCUMENTATION, made executable -- and it exists partly so nobody re-proposes the rename.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "src" / "tensor_grep" / "cli"
CONTRACTS = REPO_ROOT / "docs" / "CONTRACTS.md"

# The closed vocabulary. Every value an agent may see in `scan_limit.truncation_cause`.
#   project-files   -- the --max-files/--max-repo-files count cap. BUDGET-REMEDIABLE: raise it.
#   deadline        -- the --deadline wall-clock bound.            BUDGET-REMEDIABLE: raise it.
#   unreadable-path -- the walk (or a per-file read/stat) hit a path it could not read.
#                      NOT budget-remediable: no cap or deadline makes a denied path readable.
KNOWN_TRUNCATION_CAUSES = frozenset({"project-files", "deadline", "unreadable-path"})

# Causes that NO budget increase can fix. An agent must not retry these with a bigger limit.
NON_BUDGET_REMEDIABLE = frozenset({"unreadable-path"})

_EMITTING_MODULES = ("inventory.py", "docs_coverage.py")


def _emitted_causes(source: str) -> set[str]:
    """String literals assigned to a `truncation_cause` name anywhere in ``source``."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "truncation_cause" for t in node.targets):
            continue
        for child in ast.walk(node.value):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.add(child.value)
    return found


def _all_emitted() -> set[str]:
    causes: set[str] = set()
    for name in _EMITTING_MODULES:
        causes |= _emitted_causes((CLI_DIR / name).read_text(encoding="utf-8"))
    return causes


@pytest.mark.skipif(not CLI_DIR.is_dir(), reason="src/tensor_grep/cli absent")
def test_no_undocumented_truncation_cause_is_emitted() -> None:
    """A new cause value must be added to the vocabulary above -- and to CONTRACTS.md."""
    unexpected = sorted(_all_emitted() - KNOWN_TRUNCATION_CAUSES)
    assert not unexpected, (
        f"these `truncation_cause` values are emitted but undocumented: {unexpected}. An agent "
        "branches on this string; an unrecognised one must never default to 'raise the limit' "
        "(the fail-closed allow-list rule). Add it to KNOWN_TRUNCATION_CAUSES, say in "
        "docs/CONTRACTS.md whether it is budget-remediable, and make sure the renderer says so."
    )


@pytest.mark.skipif(not CONTRACTS.is_file(), reason="docs/CONTRACTS.md absent")
def test_every_known_cause_appears_in_the_contract() -> None:
    """The vocabulary is only useful to an agent if the contract actually lists it."""
    text = CONTRACTS.read_text(encoding="utf-8")
    missing = sorted(cause for cause in KNOWN_TRUNCATION_CAUSES if cause not in text)
    assert not missing, (
        f"these documented-in-code causes are absent from docs/CONTRACTS.md: {missing}. The "
        "contract is what an integrator reads; a value that only exists in source is not a "
        "contract."
    )


@pytest.mark.skipif(not CLI_DIR.is_dir(), reason="src/tensor_grep/cli absent")
def test_the_extractor_actually_finds_the_causes() -> None:
    """ORACLE -- prove the AST extractor works before trusting the two arms above.

    Without this, a broken extractor would return an empty set, `emitted - known` would be empty,
    and the first test would pass while inspecting nothing. A check that passes when the thing it
    guards is broken is not a check.
    """
    emitted = _all_emitted()
    assert emitted, "the extractor found NO truncation_cause values -- it is broken, not the code"
    assert "unreadable-path" in emitted, "the extractor missed a value known to be present"
    assert emitted <= KNOWN_TRUNCATION_CAUSES

    # And it must NOT match a same-named field on an unrelated object.
    assert _emitted_causes('other.truncation_cause = "not-mine"\n') == set()
    assert _emitted_causes('truncation_cause = "real"\n') == {"real"}


def test_non_budget_remediable_causes_are_a_subset_of_the_vocabulary() -> None:
    """Guard against the two sets drifting apart -- a typo here would silently disable the rule."""
    assert NON_BUDGET_REMEDIABLE <= KNOWN_TRUNCATION_CAUSES
    assert NON_BUDGET_REMEDIABLE, "if this ever empties, the budget_remediable distinction is dead"
