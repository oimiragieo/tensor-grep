# cursor-agent task: post-merge anchor re-derivation wave (2026-08-14)

You are editing the worktree `/mnt/c/dev/projects/tensor-grep/.claude/worktrees/wt-retention-20260814`
(head = origin/main 91c2220, the post-retention merge state). EDIT ONLY the files below with ONLY
the described changes. Do NO git. Do NOT run tests. Where an anchor cannot be found, print
`BLOCKER: <file> <anchor>` and continue with the others.

General rule for every edit below: keep the surrounding prose; change ONLY the cited line number,
and where the skill already has a "grep to re-derive" instruction beside the anchor, keep it.

## Edits

1. `.claude/skills/tensor-grep-validation-and-qa/SKILL.md`
   - `AGENTS.md:352,379` for `context_consistency` -> `AGENTS.md:821,848`
   - ci.yml `needs:` `:1122` -> `:1123`
   - `pyproject.toml:114-121` "CI-tested floor 3.11-3.12" -> cite `pyproject.toml:559` (`requires-python = ">=3.11"`)
   - `pyproject.toml:616` pytest-snapshot dep -> `pyproject.toml:637`

2. `.claude/skills/tensor-grep-architecture-contract/SKILL.md`
   - `_score_symbol` `:8405` -> `:8454`
   - bootstrap.py anchor cluster -> re-derive: `main_entry` 1466->1550; `_json_aggregate_blocks_passthrough` 488->572; `_requires_full_cli` 396->480; `_run_requires_ast_workflow` 1407->1491; `TG_REEXEC_GUARD` 1519->1617; `_run_rg_passthrough` 1337->1421; `_prefer_rust_first_search` OR-branch 1547->1645; `--stats` unsupported_flags 529->613
   - AGENTS.md "registration-completeness gate is BLOCKING" `:772` -> `:889`; "## Dogfood the Real Binary" `:578` -> `:957`

3. `.claude/skills/code-search-and-retrieval-reference/SKILL.md`
   - index.rs anchors: `TrigramIndex` 138->151; `FileTrigramHits` 22->30; full-scan fallback comment 1131->1609
   - flat-scorer row "now :7929" -> 8189; `_GRAPH_PAGERANK_SEED_FILE_LIMIT` "now :327" -> 335
   - "Fail closed was 444, now :1668" bullet -> 2096

4. `.claude/skills/tensor-grep-config-and-flags/SKILL.md`
   - `semantic` extra `pyproject.toml:577` -> `:627`

5. `.claude/skills/tensor-grep-semantic-search-campaign/SKILL.md`
   - `semantic` extra `pyproject.toml:620` -> `:627`

6. `.claude/skills/tensor-grep-diagnostics-and-tooling/SKILL.md`
   - command-registration "now" values: doctor 15361->15329; dogfood 15091->15059; find 4721->5043; route_test 10613->10997; `_doctor_native_frontdoor_flavor_mismatch_note` 3206->3431; `_doctor_rust_binary_remediation` 2503->2575; `_build_doctor_payload` 3227->3452

7. `.claude/skills/tensor-grep-change-control/SKILL.md`
   - AGENTS.md anchor cluster: `native_gpu_unavailable` 368->843; Operating Rules 383-389 -> heading at 856; Backend Fail-Closed rule 438-448 -> 2090; `release.yml` workflow_dispatch 838->2723; `TG_RG_TIMEOUT_SECONDS=600` 378->853; "re-run pytest/ruff/mypy in the real venv" 569->2212 (and 2241)

8. `.claude/skills/tensor-grep-release-and-positioning/SKILL.md`
   - "unselected GPUs was :367 now :469" -> 842

9. `.claude/skills/tensor-grep-large-repo-scale-campaign/SKILL.md`
   - Backend Fail-Closed Contract heading `:1672` -> `:2090`

10. `.claude/skills/tensor-grep-backlog-campaign/SKILL.md`
    - "cannot see set/list/decorator registrations was :412 now :514" -> 887

11. `.claude/skills/tensor-grep-build-and-env/SKILL.md`
    - `typer>=0.12,<0.26` at `pyproject.toml:566` -> `:567`

12. `.claude/workflows/tg-audit-fix-loop.js`
    - Find the comment `// Phases 4-5: GATE + VERIFY, looped` (or nearest equivalent) and the `MAX_ROUNDS` constant. Replace the comment with:
      `// Phases 4-5: GATE + VERIFY, looped. The gate is a fresh-context adversarial audit (independent of the fix author); verify re-probes every finding with its own commands. A FIX-FIRST verdict feeds one repair round. A104: the gate is a real-finding convergence loop and ends only on independent SHIP, never on round count -- the RUST-REPLACE-SYMLINK guard took 13 rounds plus a final codex pass to SHIP (tensor-grep-codex-gated-audit-loop, "Campaign-scale round receipts"). Budget 10+ rounds for a security-class finding; MAX_ROUNDS is a parking point, not a conclusion.`
      and change `MAX_ROUNDS` from 3 to 10.
    - In the HOUSE block after the line containing `Never `git add .` / `git add -A``, add:
      `- `git commit --amend` only while the branch has never been pushed: `git log --oneline origin/<branch>` must print nothing first; after a push, make an ordinary second commit (A110).`
      `- Before any baseline swap (`git checkout origin/main -- <file>`, Out-File/patch revert), copy the file's current uncommitted bytes aside; prefer re-editing the single mutated line back (A103).`
    - In the RED agent prompt after the env-gated seams line (A85), add:
      `Any environment-dependent SKIP branch that cannot be removed panics under an armed env var in CI (A106: the TG_REQUIRE_SYMLINK_TESTS pattern) -- a green run of silent skips proves nothing.`
      and after the anti-hang line add:
      `Bounded test handshakes use capacity-1 channels with recv_timeout on every receive; an expiry panics CANNOT_MEASURE:, never a verdict (A109) -- a capacity-0 rendezvous blocks forever.`

13. `scripts/dogfood/dogfood_features.py`
    - Strip the dead scratchpad A-scheme prefixes from comments and check labels: `# A13/#706` -> `# #706`; `# A10/#703` -> `# #703`; `-- A13` -> `-- #706`; `-- A12(a)` -> `-- dense-hint`; `-- A12(b)` -> `-- daemon-autostart`; `-- A12(d)` -> `-- prepare-out`; and in the docstring drop "A10/#703 predicate" -> "#703 predicate". (Do NOT rename any check id that a test pins; only the dead A-prefix annotations in comments/labels — if a label change would break a test assertion, print BLOCKER and skip that one.)

## Constraints

- EDIT FILES ONLY, no git, no tests.
- If you cannot find an anchor text, print `BLOCKER: <file> <anchor-description>` and move on.

## Final report

`[cursor-agent] DONE files=<N> blocked=<K>` listing changed files and any BLOCKERs. Exit 0.