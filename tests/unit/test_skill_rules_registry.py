"""Governance gate: `.claude/skill_rules.json` must stay a VALID trigger snapshot.

The file deliberately declares itself a sparse, telemetry-expanded snapshot rather than the
authoritative skill roster (`test_skill_index_sync.py` owns the roster). That sparsity is its
contract -- but "sparse" must not drift into "broken": an invalid regex silently never fires, a
dangling key routes to a skill that does not exist, and a malformed entry can make the global
`skill_activation_gate.py` hook misbehave for the whole project. This gate pins the floor:

1. The file parses and carries its declared shape (version / description / skills).
2. Every entry's selectors are mechanically sound (regexes compile; keywords are non-empty
   strings; minKeywordHits is a positive int when present; priority is a known value).
3. Every key resolves to a real tracked local skill folder, or is explicitly allowlisted as a
   GLOBAL skill (the file may seed global skills too -- AGENTS.md's skill index says so). A new
   dangling key fails here instead of failing silently at hook runtime.

What this gate does NOT check -- read before trusting a green run: it does not prove a trigger
pattern is any GOOD (that is telemetry work, per the file's own description), and it does not
require every local skill to have an entry (sparsity is the contract).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# __file__-anchored, not cwd-relative -- see test_public_docs_governance.py's flake #37 note.
_REPO_ROOT = Path(__file__).resolve().parents[2]

RULES_PATH = _REPO_ROOT / ".claude" / "skill_rules.json"
SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"

# Skills that live in the machine-global library, not this repo's .claude/skills/. The file's
# description permits seeding them; each entry here is an explicit, reviewed exception. A key
# that is neither a tracked local folder nor on this list is dangling and must fail.
_GLOBAL_SKILL_ALLOWLIST = {
    "profile-guided-byte-identical-optimization",
}

_PRIORITIES = {"high", "medium", "low"}


def _load_rules() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return loaded


def _local_skill_folders() -> set[str]:
    return {skill_doc.parent.name for skill_doc in SKILLS_DIR.glob("*/SKILL.md")}


def test_rules_file_exists_and_parses_with_declared_shape() -> None:
    assert RULES_PATH.is_file(), (
        f"{RULES_PATH} is missing -- the project-local skill activation overlay was deleted. "
        "If that was deliberate, delete this gate in the same change."
    )
    rules = _load_rules()
    assert rules.get("version"), "skill_rules.json lost its version field"
    assert rules.get("description"), "skill_rules.json lost its description field"
    skills = rules.get("skills")
    assert isinstance(skills, dict) and skills, (
        "skill_rules.json carries no skills map -- an empty overlay is either a mistake or the "
        "file should be deleted outright, not left inert."
    )


def test_rules_file_positive_control_sees_the_local_library() -> None:
    # Bidirectional-oracle guard: if SKILLS_DIR or the glob broke, the dangling-key check below
    # would mark every local key dangling for the wrong reason, or the allowance would vacuously
    # pass. Fail loudly on an implausibly small folder count instead of trusting the silence.
    assert len(_local_skill_folders()) >= 15, (
        "Only a handful of .claude/skills/*/SKILL.md folders resolved -- the glob or SKILLS_DIR "
        "is probably broken, not the rules file."
    )


def test_every_rule_key_resolves_to_a_real_skill() -> None:
    rules = _load_rules()
    local = _local_skill_folders()
    dangling = sorted(
        key for key in rules["skills"] if key not in local and key not in _GLOBAL_SKILL_ALLOWLIST
    )
    assert not dangling, (
        "skill_rules.json seeds triggers for skills that exist neither in .claude/skills/ nor in "
        f"the reviewed global allowlist: {dangling}. Rename the key to the real folder, or add a "
        "reviewed entry to _GLOBAL_SKILL_ALLOWLIST in this test if it is a machine-global skill."
    )


def test_every_rule_entry_is_mechanically_sound() -> None:
    rules = _load_rules()
    problems: list[str] = []
    for key, entry in rules["skills"].items():
        if not isinstance(entry, dict):
            problems.append(f"{key}: entry is not an object")
            continue
        priority = entry.get("priority")
        if priority is not None and priority not in _PRIORITIES:
            problems.append(f"{key}: unknown priority {priority!r}")
        patterns = entry.get("intentPatterns")
        if patterns is not None:
            if not isinstance(patterns, list) or not patterns:
                problems.append(f"{key}: intentPatterns must be a non-empty list")
            else:
                for pattern in patterns:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        problems.append(f"{key}: intentPattern {pattern!r} does not compile: {exc}")
        keywords = entry.get("keywords")
        if keywords is not None:
            if not isinstance(keywords, list) or not keywords:
                problems.append(f"{key}: keywords must be a non-empty list")
            elif any(not isinstance(k, str) or not k.strip() for k in keywords):
                problems.append(f"{key}: keywords must be non-empty strings")
        min_hits = entry.get("minKeywordHits")
        if min_hits is not None and (not isinstance(min_hits, int) or min_hits < 1):
            problems.append(f"{key}: minKeywordHits must be a positive integer")
        if patterns is None and keywords is None:
            problems.append(f"{key}: entry selects nothing (no intentPatterns and no keywords)")
    assert not problems, "skill_rules.json entries are mechanically broken:\n  " + "\n  ".join(
        problems
    )
