# SKILL AUDIT FINDINGS — consolidated fix list (2026-08-11)

Source: 3-wave parallel audit of all 33 skills vs verified factsheet
(docs/audits/2026-08-11-skill-audit-facts.md). Each finding: file:line, stale, fix.

## Stale version stamps (must be ≥ v1.110.14 or "v1.110.14+")
1. .claude/skills/tensor-grep/SKILL.md:104 "v1.110.13" → "v1.110.14"
2. .claude/skills/tensor-grep/SKILL.md:43 "@ 1.110.13" → "@ 1.110.14+"
3. .claude/skills/tensor-grep-workspace-dogfood/SKILL.md:12 "==1.110.13" → "==1.110.14"
4. .claude/skills/tensor-grep-workspace-dogfood/SKILL.md:18 "(v1.110.13)" → "(v1.110.14)"
5. .claude/skills/tensor-grep-workspace-dogfood/SKILL.md:44 "tg 1.110.13" → "tg 1.110.14"
6. .claude/skills/tensor-grep-prepare/SKILL.md:8 "tg 1.110.13" → "tg 1.110.14"
7. .claude/skills/tensor-grep-prepare/SKILL.md:9 dangling contradiction → delete
8. .claude/skills/tensor-grep-find-and-route/SKILL.md:8 "1.95.0" → "1.110.14"
9. .claude/skills/tensor-grep-multi-project-search/SKILL.md:10 "1.110.13" → "1.110.14"
10. .claude/skills/tensor-grep-ledger/SKILL.md:8 "1.101.31" → "1.110.14"
11. .claude/skills/tensor-grep-enterprise-agent/SKILL.md:8 "1.98.25" → "1.110.14"
12. .claude/skills/tensor-grep-add-language/SKILL.md:30 "v1.98.2 / ba63aa0" → "v1.110.14 / a6242bb"
13. .claude/skills/tensor-grep-enterprise-review-bundle/SKILL.md:8 "1.95.0" → "1.110.14"
14. .claude/skills/tensor-grep-gpu/SKILL.md:8 "1.95.0" → "1.110.14"
15. .claude/skills/tensor-grep-large-repo-scale-campaign/SKILL.md:14 "v1.96.0" → "v1.110.14"
16. .claude/skills/tensor-grep-architecture-contract/SKILL.md:295 "1.95.0" → "1.110.14"
17. .claude/skills/tensor-grep-diagnostics-and-tooling/SKILL.md:288+291 "1.17.25" → "1.110.14"

## Language-tier contradictions (ALL 10 parser-backed, foundational EMPTY)
18. code-search-and-retrieval-reference/SKILL.md:771 "5 parser-backed" → SUPERSEDED note: 10 parser-backed
19. code-search-and-retrieval-reference/SKILL.md:796 "8 parser-backed" → SUPERSEDED note: 10 parser-backed
20. code-search-and-retrieval-reference/SKILL.md:803 "9 parser-backed" → SUPERSEDED note: all 10 parser-backed
21. tensor-grep-add-language/SKILL.md:40 "foundational tier as Java/PHP/C#" → mark SUPERSEDED (all parser-backed now)
22. tensor-grep-failure-archaeology/SKILL.md:485 (x2) "all eight" → "all ten" tree-sitter packages

## State staleness (M17 merged)
23. tensor-grep-cross-platform-path-confinement/SKILL.md:142 "M17 on audit/m17-* PR head" → merged on main
24. tensor-grep-index-fingerprint-freshness/SKILL.md:30 "M17 ... PR head" → merged on main

## Doctor schema-3 gaps
25. tensor-grep-config-and-flags/SKILL.md env catalog → add TG_DOCTOR_OFFLINE row
26. tensor-grep-diagnostics-and-tooling/SKILL.md:84 "Load-bearing fields" table → add pypi_latest/
    installed_behind_pypi/shadow_launchers/installation_health
27. tensor-grep-diagnostics-and-tooling/SKILL.md:80 "straight field dump" → now appends health warning

## Verified clean (no findings)
tensor-grep-run-and-operate, change-control, debugging-playbook, build-and-env,
argv-normalization-and-shadowing, hermetic-hostile-tests, codex-gated-audit-loop,
backlog-campaign, benchmark-and-proof-toolkit, docs-and-writing, semantic-search-campaign,
validation-and-qa, research-frontier, research-methodology, release-and-positioning,
enterprise-review-bundle (only stamp), find-and-route (only stamp).

## NEW SKILL (decision, Exa-grounded)
Create ONE new skill: tensor-grep-release-drift-check — a mechanical post-release governance
sweep: (a) version stamps ≥ current tag across skills/docs, (b) derived counts (language tier via
_symbol_navigation_descriptor, skill count, tree-sitter package count), (c) known-state facts
(M17 merged, doctor schema-3), (d) sample commands to re-derive each. Rationale: this session
found 21 stale stamps + 7 tier contradictions ONE release after the last refresh — manual
curation does not scale (Google Agent Skills: "skill is a living product"; Anthropic:
evaluation-driven maintenance). NOT a pytest (numbers drift; a hard gate would red every PR);
a maintenance command akin to .claude/skill_anchor_audit.py.