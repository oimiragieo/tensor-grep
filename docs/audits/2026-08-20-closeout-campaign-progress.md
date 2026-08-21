# 2026-08-20 worldclass-closeout campaign — progress (jr-analyst handoff)

This document is the pick-up point for the closeout campaign. It assumes no prior context.

## 1. What this campaign is

The council-approved plan lives at `docs/plans/2026-08-20-worldclass-closeout-plan.md`
(merged in PR #1055 after a 4-round thinktank loop ending in a unanimous 7/7 APPROVE; the
approved artifact hash is recorded in the plan's own audit trail). It defines 19 canonical
items (`W1-a` .. `W6-b`, plan section 1.5) and a total merge order (plan section 2) that
must be followed one PR at a time. Do not re-derive scope from this file — the plan's
canonical item registry is the definition of "campaign complete".

## 2. Scoreboard

| Item | State | Receipt |
|---|---|---|
| W1-d handler census + disposition gate | MERGED | PR #1056 |
| W2-a MCP 2.0 decision record (six reopen triggers) | MERGED | PR #1057 |
| W2-b mcp floor bump 1.27.2 -> 1.29.0 | MERGED, `fix:` release in flight | PR #1061, run 32426087438 |
| W2-c tested maintenance probe | MERGED | PR #1060 |
| W3-a beyond-Route-A costing (all three ESCALATE) | MERGED | PR #1058 |
| W6-a tg-ledger rebuild guide | MERGED | PR #1059 |
| W6-b rebuild-guides README entry | SATISFIED by W6-a's merge | acceptance one-liner (section 4) passed against main `88ecd58` |
| W1-a 57-handler mcp_server audit | IN FLIGHT (agent building RED-first arms) | opens its own PR; codex A3 gate required before merge (plan section 4.2.1) |
| W1-b, W1-c | HELD — serialized behind W1-a | plan section W1.2 (single writer on the shared gate file) |
| W4-a python_sidecar.rs test extraction | PR OPEN, includes the W4-f allowlist retirement | PR #1063 (merge slot: step 7) |
| W4-b .. W4-e, W4-f pin steps | HELD — total merge order steps 8-19 | plan section 2 |
| W5-a, W5-b Rust test extractions | HELD — after W4 in the shared Rust CI lane; A3 gate on W5-a | plan sections 2 and 4.2.1 |
| Route-B ESCALATE decisions (3 modules) | HELD behind W1 completion | `docs/design/2026-08-20-beyond-route-a.md` |
| Closeout manifest + BACKLOG.md findings append | Campaign end | plan section 4.8 |

Also open: PR #1062 (docs, skill lessons) — green, deliberately held until the release run
completes (push-race law: never merge anything into main while a `Semantic Release` job is
in flight).

## 3. What happens next, in order

1. The release run for `88ecd58` (#1061) completes; verify the published wheel on PyPI and
   dogfood it (`uvx --from tensor-grep==<new version> tg --version`, then the sweep in
   `.claude/skills/tensor-grep-workspace-dogfood/SKILL.md`).
2. Merge held PR #1062.
3. W1-a's PR arrives -> verify its report against the branch -> codex A3 adversarial gate
   (plan section 4.2.1) -> merge -> dispatch W1-b, then W1-c (same gate file, one writer).
4. Merge PR #1063 at its slot (step 7). Its allowlist retirement is already inside the PR,
   per the collision map's "same PR as the change that moved the count" rule.
5. Continue the merge order: W4-d (witness commit FIRST, plan section W4.4), then the
   W5/W4 Rust chain with a W4-f pin step after every Rust merge, without exception.
6. After W1 completes: take the three Route-B decisions using the merged costing doc.
7. Closeout manifest (plan section 4.8): re-run every acceptance, append new findings to
   `docs/BACKLOG.md`.

## 4. How to verify any claim here

- Scoreboard PRs: `gh pr view <n> --json title,state,mergedAt`.
- Release class of any merge: `git log --format='%s' <last-tag>..origin/main` and grep
  `^(fix|feat|perf)` — the merged COMMIT SUBJECT is the release semantic, never the PR title.
- W6-b acceptance (from plan section W6.3):
  `python -c "import re,pathlib,sys; t=pathlib.Path('docs/rebuild-guides/README.md').read_text(encoding='utf-8'); has=t.count('tg-ledger.md'); counts=re.findall(r'there are \d+|\d+ (?:rebuild )?guides',t); print('entry',has,'count-sentences',counts); sys.exit(0 if has==1 and not counts else 1)"`
- Size gate state on any branch: `python scripts/file_size_budget.py --report`.
- The research context feeding W2: `docs/audits/2026-08-20-research-receipts.md`.

## 5. Open risks

- The W4/W5 Rust chain cannot be compiled locally (shared box — CI is the compiler); every
  extraction is textual and `test-rust-core` is the arbiter, so a mistake surfaces only in CI.
- Two `[REL]` windows remain in the merge order; each needs its own clean release window.
- W1-b/W1-c briefs must carry the shared-surface reconciliation duty (see
  `tensor-grep-execution-wave-lessons-2026-08-20` in session memory): when a later slice owns
  the tested form of something an earlier slice stubbed, the later slice reconciles it.
