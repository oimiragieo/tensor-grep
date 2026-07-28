# tensor-grep — Task Board

> **The operational one-pager.** `docs/BACKLOG.md` is the historical ledger (long, append-only,
> release-by-release); THIS file is the live queue a session or subagent works down, one item at a
> time. Keep it in sync with the CLI task store (`TaskList` / `TaskUpdate`) and with
> `gh pr list` — GitHub is the source of truth for PR state, this file is the source of truth for
> WHAT IS NEXT AND WHY.
>
> **Rules for anyone (human or agent) working this board:**
> 1. Take the top unblocked item in the highest-priority section. Do not cherry-pick easy ones.
> 2. Every item needs a **bidirectional oracle** before it is done — state what the test shows on
>    the PRE-FIX baseline. A test that passes in both arms is not evidence (AGENTS.md, oracle forms).
> 3. Move an item to DONE only with a PR number AND a merged commit. "Verified locally" is not done.
> 4. If an item turns out to be a non-defect, move it to **RETIRED** with the reason — a documented
>    retirement is worth as much as a fix, because it stops the next session re-chasing it.
> 5. `fix:`/`feat:` PRs RELEASE. Merge one per publish and wait for it. `docs:`/`test:`/`chore:`
>    do not release and may batch. **The merge gate is "no runs in flight on main", full stop** —
>    `tag == PyPI` cannot distinguish *released* from *not started* from *died* and cost a release
>    on 2026-07-28.

Last reconciled: **2026-07-28**, post-**v1.101.9**.

---

## IN FLIGHT (PRs open right now)

| PR | Title | Type | State |
|---|---|---|---|
| #843 | `fix(cli)`: a `--json` scan refusal must be parseable, not zero bytes on stdout | RELEASING | CI running |
| #844 | `docs(skills)`: land the 1.101.9 live-dogfood corrections | docs | CI running |

---

## P1 — external dogfood findings (a real user hit these)

Reported against 1.101.7 and re-confirmed on 1.101.9, so none of these are stale.

- [ ] **Anonymous `--claim`** — `tg prepare --claim` submits with an anonymous `agent_id` unless
  `TG_LEDGER_AGENT_ID` is exported. The ledger's entire purpose is multi-agent coordination, and an
  anonymous claim cannot be attributed or released by its owner. **Fork to decide first:** refuse
  without a resolvable identity (fail-closed, matches the repo's allow-list discipline) vs derive a
  stable default (friendlier, but invents identity semantics). *Task #13.*
- [ ] **Ledger Slice 2 rollup parity** — Slice 1 (`claim`/`list`/`release`) does subtree rollup;
  Slice 2 (`record`/`find`) matches path-literally, so a finding recorded in a subdir is not found
  from the parent. Check whether content-addressing makes rollup ambiguous *before* changing match
  semantics — Slice 2 is content-addressed where Slice 1 is path-scoped. *Task #14.*
- [ ] **MaxSim late-rerank is advertised but unexercised** — named in `tg find --help`, absent from
  the `install-dense` CUJ docs and tests. A capability the artifact claims and nothing proves is the
  same class as a stamped-but-unpublished version. Acceptance: a test asserting the rerank stage was
  TAKEN, not merely that the flag parsed. *Task #15.*
- [ ] **Bare `tg search P --json` with no PATH** — reporter sees `exit 1`, empty, no refuse on their
  repo. **NOT reproduced here** on 1.101.7 or 1.101.9 with a checksum-verified native binary and
  delegation confirmed firing (`routing_backend=NativeCpuBackend`). The reproducible half — refusal
  emitting zero bytes on the `--json` surface — is fixed in #843. To pin the rest, capture from the
  reporting host: `tg doctor --json` (`native_tg_binary_exists`), repo file count, exact cwd.

## P2 — audit queue (deep audit `wf_38d4b580-d89`, 2026-07-28)

Six read-only lenses — security, CI/release workflow, disclosure edge cases, dead/unwired code,
test trustworthiness, scale-correctness — each finding adversarially verified before it lands here.

- [ ] *(populated from the audit's chairman synthesis — see the run's queue output)*

## P3 — strategic / positioning (informed by Exa competitive research, 2026-07-28)

- [ ] **Articulate the policy layer as the moat.** The 2026 market consensus is that lexical +
  structural + graph are all table stakes and *"there is no shortage of tools, there is a shortage
  of POLICY — the orchestration layer that combines all three with escalation and budget control is
  the real gap."* `tg prepare` and `tg agent` ARE that layer, and `docs/tool_comparison.md` still
  positions tg mostly as a search comparator. Reframe around one-call edit readiness.
- [ ] **Name incompleteness-honesty as a differentiator.** No competitor surveyed (Gortex, Serena,
  claude-context, grepai, CodeGraph, Sourcegraph, Augment) documents a contract of the form
  *"a surface that cannot finish must say so, in a machine-branchable field, with the exit code
  agreeing."* agentmako's freshness labels (live/fresh_indexed/stale/contradicted/unknown) are the
  nearest analogue and are weaker. This is a real moat and it is currently invisible outside the repo.
- [ ] **Token-economics is the category's scoring metric.** Competitors publish token-reduction
  numbers (grepai 97% input-token cut, CodeGraph ~70% fewer tool calls, GitNexus 88%, Gortex 3–50×).
  tg's own measured **7.5× fewer tokens than grep** is the same metric family. Publication is
  **CEO-gated (#72)** — not an AI-doable item, listed so it is not forgotten.
- [ ] **Language coverage gap, stated honestly.** tg: 10 registered / 5 parser-backed caller graph.
  Gortex claims 257 languages, Serena 40+. tg's are *deeper* (resolved edges vs shape matching), so
  the honest frame is depth-vs-breadth — but the breadth number will be used against it.

## P4 — carried backlog (from `docs/BACKLOG.md`, still open)

- [ ] **#58** promote `tg route-test` hidden → public
- [ ] **#98** MCP tool consolidation (45 → ~10 task-shaped dispatch tools, non-breaking)
- [ ] **#141** native `AstBackend` vs ast-grep wrapper — DSL divergence
- [ ] **#160** v1.71.3 dogfood feature tail (`suggested_ignore`, orient auto-deweight)
- [ ] **#115** symlink sweep — 3 unguarded `std::fs::write` sites *(LOW)*
- [ ] **#125** checkpoint `except Exception` → `except BaseException` *(LOW)*
- [ ] **#143 / #155** Opus-gate LOW follow-ups *(LOW)*
- [ ] Dead code: delete `sidecar.py::_classify_lines` *(LOW)*

## BLOCKED — environment (not CEO-gated, just needs hardware)

- [ ] **#89** WSL `/mnt/c` absolute-path resolution in the native backend
- [ ] **#90** `tg scan` ast-grep Linux/WSL portability + doctor false-"available" exit-127
- [ ] **#109** CUDA GPU implicit-walk ceiling

## CEO-GATED (do not start without an explicit go)

- [ ] **#72** publish the benchmark proof-point (7.5× fewer tokens than grep) — public claim
- [ ] **#131 / #169** GPU deep-dive + multi-week rebuild; CUDA asset publishing is on a deliberate
  HOLD. Phase-0 shipped correctness-proven assets gated OFF by
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`; the flag-flip is the CEO's call.
- [ ] **#48** public-shim startup overhead — closed as an honest NEGATIVE (tg's native walk *is*
  rg's walk, same `ignore` crate, so widening relocates cost rather than removing it). The
  architectural remainder is a CEO scoping call.
- [ ] **#77** `tg ledger` local agent context-sharing — approved in principle, scope gated

---

## RETIRED (do not re-chase — each cost a real cycle to settle)

| Idea | Why it is dead |
|---|---|
| `HashSet<PathBuf>` distinct-path counter | The code it would edit documents the design as rejected: unbounded per-path Vec behind a mutex is a contention point AND a DoS surface (50k unreadable entries → 50k-entry payload). Also breaks byte-reproducibility. |
| Rename `incomplete_paths_count` | Its zero-cost precondition expired when the field shipped in v1.99.5; a published field is a 90-day dual-emit exercise, not a two-line diff. |
| `SearchStats::is_empty()` as a live bug | The guarded state is provably unreachable — every writer of `binary_match_files` is preceded by `searched_files += 1`. |
| cAST structural chunking | Real-corpus eval: net-wash quality, 24.4× slower. |
| Dense int8/binary/PCA embedding compression | Retired on measurement. |
| GPU-for-search crossover | Re-adjudicated 2026-07-21 across 10MB–5GB: no crossover at any scale; the shipped kernel is a position-parallel brute-force byte-compare, not PFAC. |
| "Beat rg on cold search" | Closed as an honest negative — tg's native walk IS rg's walk (same `ignore` crate). The campaign's return was a defect family, not milliseconds. |

---

## Reference

- Historical ledger: `docs/BACKLOG.md` · Contracts: `docs/CONTRACTS.md` · Laws: `AGENTS.md`
- Release mechanics + positioning rules: `.claude/skills/tensor-grep-release-and-positioning`
- What counts as proof: `.claude/skills/tensor-grep-validation-and-qa` (oracle forms 1–10)
