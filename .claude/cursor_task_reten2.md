# cursor-agent task: skill-library retention fixes (2026-08-13b)

You are editing a FRESH worktree of tensor-grep:
`/mnt/c/dev/projects/tensor-grep/.claude/worktrees/wt-retention-20260813b`.
Edit ONLY the files listed below with ONLY the described changes. Do NO git. Do NOT run tests.
Follow each edit precisely; where an edit says "append a note", keep existing text and add the note.

## Edits

### 1. .claude/skills/tensor-grep-cross-platform-path-confinement/SKILL.md

a. Find every claim in the file of the form "junctions are NOT symlinks" / "`Path.is_symlink()` is False on a junction" / "a junction != a symlink". Beside EACH such claim (inline, right after the sentence), add:

` **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**`

b. Find the sentence claiming `mklink /J` silently fails when the target directory is NON-EMPTY. Reword it to: the LINK path must not already exist; the target directory MAY be populated.

c. In Part 5 (where A88 is discussed), append a new subsection at the end of Part 5, before the next Part heading:

```
### Settling contested platform facts with a bounded probe (2026-08-13, A107)

When review seats assert opposite facts about a toolchain-version-dependent platform behavior,
do not re-vote — probe. A tiny std-only `cargo run --release` program (positive + negative
controls: known-symlink and known-regular fixtures) on the PINNED toolchain settled the
junction question for Rust 1.96.0 in ~30s and became the only artifact all seats cite
(probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5).
If the result supersedes a documented claim (e.g. A88's "junctions are NOT symlinks"), the
superseded claim itself carries an append-only SUPERSEDED note — never silently rewritten (A94).

### Summary

Move the existing "quick reference" or summary block content here unchanged if there is one;
otherwise skip this section.
```

Only add the English subsection; do NOT create a "### Summary" if the file has none — ignore that last block instruction and do not write it.

### 2. .claude/skills/tensor-grep-hermetic-hostile-tests/SKILL.md

Find each claim of the form "a junction is NOT a symlink: `Path.is_symlink()` is False" (including the `assert not link.is_symlink()` pin description). Beside each, add the same SUPERSEDED note as in edit 1a, and scope the `assert` pin sentence with "for CPython pathlib" if it refers to Python.

### 3. .claude/skills/tensor-grep-debugging-playbook/SKILL.md

a. Find the A101 probe description (public-version-powershell flaked 3x, 30s timeout). After that description, add:

` **FIXED: PR #1009 → v1.110.15 — scripts/agent_readiness.py `Check.retry_on_timeout` (opt-in, clamped at `_MAX_TIMEOUT_RETRIES = 3`) + the four shell probes timeout_s=90 + retry_on_timeout=1; `attempts` in every run_check result.**`

b. Find the anchor `main.py:1894` for `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`. Update it to `main.py:1966` and append " (re-derive with: grep -n '_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS' src/tensor_grep/cli/main.py)".

c. Find the anchor `repo_map.py:2126` for `_python_imports_and_symbols`. Update to `repo_map.py:2166` with the same grep-the-symbol note.

### 4. .claude/skills/tensor-grep-validation-and-qa/SKILL.md

a. Same `repo_map.py:2126` → `:2166` fix for `_python_imports_and_symbols`.
b. `agent_readiness.py` `main()` `:1155-1237` → real is `def main` at `:1258`; exit-code line `:1233` → `:1336`. Update both, with grep-the-symbol notes.
c. `ci.yml:943` for the Semantic Release `needs:` list → real is the `release:` job at `ci.yml:1121` with `needs:` at `:1122`. Update.
d. `AGENTS.md:352,379` for `context_consistency` → `AGENTS.md:710,737`. Update.

### 5. .claude/skills/code-search-and-retrieval-reference/SKILL.md

a. The move-chain note "was `:6692-6697` in `main.py`, then `:6952-6957`" — append: ` now `ast_workflows.py:1231-1232` (grep "Prefer the ast-grep wrapper").`
b. `_personalized_reverse_import_pagerank` at `repo_map.py:8914` → `repo_map.py:9174` (grep-the-symbol).

### 6. .claude/skills/tensor-grep-architecture-contract/SKILL.md

Update the scorer anchors `_score_text_terms:7912` / `_score_file_path:8100` / `_score_symbol:8177` to `:8189` / `:8377` / `:8405` respectively; add "re-derive with: grep -n 'def _score_' src/tensor_grep/cli/repo_map.py | sort" beside them.

### 7. .claude/skills/tensor-grep-release-and-positioning/SKILL.md

The ci.yml sticky anchors `:1518`/`:1539`/`:1416`/`:1453` → update to `:1475`/`:1512` (publish jobs) and `:1577`/`:1598` (`publish-success-gate`), with the existing "grep the symbol" guidance kept.

### 8. .claude/skills/tensor-grep-research-frontier/SKILL.md

`_symbol_record()` at `repo_map.py:2359` → `:2399`, and the `"file"` field nearby; grep-the-symbol note.

### 9. .claude/skills/tensor-grep-diagnostics-and-tooling/SKILL.md

`validate_docs_claims` at `scripts/agent_readiness.py:623` → `:634` (append "now :634" keeping the drift note shape).

### 10. .claude/skills/tensor-grep-docs-and-writing/SKILL.md

`validate_docs_claims` at `agent_readiness.py:623` → `:634`.

### 11. .claude/skills/tensor-grep-add-language/SKILL.md

The pyproject `ast` extra drift note "`:600`→`:614`" → append " now `:621`".

### 12. .claude/skills/tensor-grep-index-fingerprint-freshness/SKILL.md

"current tag v1.110.14" → "current tag v1.110.16 (re-derive with: git describe --tags origin/main)".

### 13. .claude/skills/tensor-grep/SKILL.md

"Latest CUJ dogfood: v1.110.14" → "Latest CUJ dogfood: v1.110.14 (unchanged through v1.110.16; re-run the CUJ on the published wheel before the next restamp)".

### 14. .claude/skills/tensor-grep-codex-gated-audit-loop/SKILL.md

Append this section at the END of the file:

```
## Campaign-scale round receipts (2026-08-13)

A campaign-scale A3 security gate (the RUST-REPLACE-SYMLINK symlink/junction guard, PR #1010)
ran **13 opus rounds plus a final codex pass** to SHIP. Lessons that generalize:

- **The adversarial bar is STRICTER than the merge-readiness bar.** Termination happens when the
  remaining findings are cosmetic/honesty-class, not when the gate returns zero findings
  (published receipt: one-shot review approval 43% vs iterative adversarial loop 91%, where the
  adversarial reviewer never reached zero findings yet an independent reviewer approved 7/8 —
  github.com/kimjune01/refactor-equivalence). Plan on 2-5 codex rounds for a routine item, but
  budget 10+ for a security-class item; the terminator is findings-CLASS, not round count.
- **The gate reliably surfaces a small defect taxonomy.** Five classes recurred across the 13
  rounds: (1) test-fidelity seams (a fault injected BEFORE the stat can never observe a fail-open
  rewrite — inject the fault INTO the same `map_err` path the production stat uses); (2)
  scope-honesty residuals (a leaf-only `lstat` leaves non-leaf ancestor links, the directory-ROOT
  swap window, and trailing-separator resolves through — name each with a filed owner row); (3)
  board-code consistency (codethat cites a board row must file/update the row in the SAME PR, and
  the IN_FLIGHT transition belongs in the implementation PR per A50); (4) skip-visibility (a green
  test that silently skips proves nothing — promote skips to panics via an env var armed in CI,
  e.g. TG_REQUIRE_SYMLINK_TESTS); (5) commentary accuracy (Send/Sync claims, deferral rationale,
  Disconnected-vs-Timeout attribution — the rounds keep finding these until every load-bearing
  comment is re-derived). Check each class explicitly before opening the gate.
- **Gate-vendor generalization.** codex (gpt-5.6-sol) is the nominal vendor; opus (claude -p
  --model opus) is the reliable A3 substitute on this box. The per-round contract stays identical:
  read-only, cite file:line, verdict SHIP | SHIP-WITH-NIT | FIX-FIRST(+file:line+repro+minimal fix),
  and every fix lands as its OWN commit on the PR branch (unpushed branches never amend — A110).
- **Stopping rule from the literature:** one clean round is not proof of convergence on a
  stochastic adversarial gate; prefer a two-consecutive-clean-pass criterion with the second pass
  run by a DIFFERENT vendor (arxiv.org/html/2605.12280).
```

### 15. .claude/skills/tensor-grep-backlog-campaign/SKILL.md

In the Phase 4 ("Council review") step, add one line after the existing thinktank step:

`- Multi-round hash-frozen approval loop (2026-08-13 receipt): freeze the artifact hash, run the 8-seat council, apply ONLY the verified findings, re-hash, re-run until N/N APPROVE (5 rounds to 7/7 on docs/plans/2026-08-13-backlog-completion-plan.md). Failed seats are not votes. A step whose content depends on a future verdict must be written NOW as a named GATE with command + trigger + re-approval rule — "EXPAND AT WAVE START" reads as a placeholder to half the council. Cite the artifact hash in every round's question file. (A108)`

## Constraints

- EDIT FILES ONLY. Do NO git. Do NOT run tests. Do NOT touch any file not named above.
- Preserve all other text; no reformatting of untouched lines.
- Where you cannot find the described anchor, DO NOT improvise a change — print `BLOCKER: <file> <anchor description>`.

## Final report

Print `[cursor-agent] DONE files=<count> ready_for_audit` listing which files changed, or
`BLOCKER: <details>` per missed anchor. Exit 0 on done.