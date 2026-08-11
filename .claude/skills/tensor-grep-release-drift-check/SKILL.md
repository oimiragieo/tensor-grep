---
name: tensor-grep-release-drift-check
description: >-
  Run a mechanical post-release governance sweep of the in-repo skills and docs after a release
  lands — version stamps that fall below the current tag, derived counts (language tier via
  _symbol_navigation_descriptor, skill count, tree-sitter package count) that no longer match the
  code, and known-state facts (merged PRs, doctor schema fields) that went stale. Use when a
  release just merged (e.g. "we hit v1.110.x, are the skills/docs current?"), when auditing the
  in-repo skill library for drift, when a skill still claims a tier/count a newer skill or this
  session's changes superseded, or before writing new skills that would inherit a stale stamp.
  NOT a pytest (numbers drift; a hard gate would red every PR) — a maintenance sweep like
  .claude/skill_anchor_audit.py. Sibling of tensor-grep-docs-and-writing (the docs governance) and
  tensor-grep-release-and-positioning (the release mechanics themselves); this one is the
  bookkeeping that keeps the library current after the release.
---

# tensor-grep: post-release skill/docs drift check

A release is not "done" when PyPI shows the version. The in-repo skill library is a snapshot of
what an engineer believed when each line was written; after a release, some of it is now wrong.
This sweep finds those and gives you the exact command to re-derive each fact so the fix is a
measurement, not a guess.

Ground truth holder: `docs/audits/2026-08-11-skill-audit-facts.md` (the last full sweep) and the
`docs/audits/2026-08-11-skill-audit-findings.md` ledger it produced.

## When NOT to use this skill

| Situation | Use instead |
|---|---|
| Writing or editing the prose of a governed doc | `tensor-grep-docs-and-writing` |
| Merging a release-bearing PR / pushing a tag | `tensor-grep-release-and-positioning` |
| A skill's `file:line` anchors don't resolve | `tensor-grep-change-control` (the anchor audit is documented there) |
| Deciding whether a NEW skill is worth creating | research/deep-dive process, then create-skill conventions |

---

## Part 1 — Run it (the ~2 minute sweep)

1. **Version stamps.** Every skill carries a "verified against vX.Y.Z" / "As of vX.Y.Z" / "current
   tag vX.Y.Z" line. Grep the whole library and list every stamp that is strictly below the
   current tag (`git describe --tags --abbrev=0`):

   ```bash
   grep -rn "v1\.1[0-9][0-9]\.[0-9]*" .claude/skills/*/SKILL.md
   ```

   A stamp below current tag is not auto-broken (a skill about an OLD release is fine), but a
   stamp that claims to describe CURRENT behavior must be at or above the tag. The 2026-08-11 sweep
   found 21 stale stamps ONE release after the last refresh — manual curation does not scale.

2. **Derived counts** — re-derive, never hand-count (a count is a measurement, and hand-counts
   have been wrong every pass; see `tensor-grep-enterprise-agent`'s "Never hand-count this" rule):

   - Language tier (`10 parser-backed / 0 foundational` at v1.110.14):

     ```bash
     python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
     ```

     If a skill still says "5 parser-backed / 5 foundational" or "8", it is superseded — append a
     dated SUPERSEDED note (see below), do not silently edit the old sentence.

   - Skill library count (the `**N skills**` figure in AGENTS.md/CLAUDE.md): it equals the number
     of `.claude/skills/*/SKILL.md` folders named `tensor-grep-*` plus
     `code-search-and-retrieval-reference` (the bare `tensor-grep` usage skill is deliberately NOT
     counted — off-by-one otherwise). Governance-pinned by `test_skill_library_drift.py`.

   - `tree-sitter-*` package count (should be 10 after C/C++ joined; grep the `pyproject.toml` ast
     extra and `uv.lock`).

3. **Known-state facts.** Grep for the release's load-bearing facts and confirm each against code:
   doctor schema fields (`pypi_latest`, `installed_behind_pypi`, `shadow_launchers`,
   `installation_health`, env `TG_DOCTOR_OFFLINE` — v1.110.14), the merged PR numbers, and any
   new env vars. A fact that is now false is a SUPERSEDED candidate.

## Part 2 — Fix it (append-only SUPERSEDED, never rewrite-as-if-new)

Precedent and law: this repo's `code-search-and-retrieval-reference` Task-10D/10E notes are the
model. When a skill's old claim is WRONG but was correct-as-dated:

- Leave the old sentence untouched (it is dated history and its read is accurate for its time).
- Append a **`SUPERSEDED (append-only, dated)`** block immediately after it: what changed, at what
  release/commit, and how it was re-derived (paste the command output shape).
- Update the "verified against" stamp to the current tag in `Part 1`-1style line so the NEXT
  sweep's grep flags it as current.

Do NOT:

- Re-stamp the same line number/sentence silently (the anchor-audit law).
- Delete the old claim entirely (a reader hitting a feature reads "fixed" and stops — the
  dangerous shape is a doc asserting something is BROKEN when it is fixed, or claiming an OLD tier
  where a NEW one exists).
- Turn this into a hard pytest. The numbers drift by design; a hard gate reddens every PR. This is
  a maintenance command, like `.claude/skill_anchor_audit.py`, not a CI assertion.

## Part 3 — Register new/changed skills

Adding a skill folder (`tensor-grep-release-drift-check` being an example) requires, or this gate
goes red and future readers never find it:

1. Name the folder in BOTH `AGENTS.md` and `CLAUDE.md` Skills sections (exact-set gate:
   `test_skill_index_sync.py` — every real folder must be named, no phantoms, and the two docs
   must name the same set).
2. Bump the `**N skills**` count in BOTH docs to the new library count (`test_skill_library_drift.py`).
3. Ensure every `file:line` citation in the new skill resolves to a git-tracked file with a line
   in range (`test_skill_library_drift.py` scans ALL of `.claude/skills/*.md`).
4. If the skill is a trigger-rule candidate, add its trigger keywords to `.claude/skill_rules.json`
   (harness config only; not a product contract; invisible to the two tests above — it has no SKILL.md).
5. Keep the folder set, the two docs' indices, and the count mutually consistent; the sweep in
   Part 1 is exactly how a future session re-verifies all three.

## Receipts

- 2026-08-11 sweep (v1.110.14): 21 stale stamps, 7 tier contradictions, 2 stale state facts
  (doctor schema, index-fingerprint) — all corrected or SUPERSEDED, and this skill created as the
  standing maintenance mechanism. Ledger: `docs/audits/2026-08-11-skill-audit-findings.md`.
