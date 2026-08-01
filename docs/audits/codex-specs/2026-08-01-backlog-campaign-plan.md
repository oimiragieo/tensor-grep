# codex task: verify the open tensor-grep backlog against real code, then propose a prioritized implementation plan

You are running as `codex exec` in non-interactive mode against the `main` branch of the
**tensor-grep** repo at `C:\dev\projects\tensor-grep`. **Do NOT modify any code. Do NOT git
add/commit/push.** Produce a single markdown report at the output path below.

You are one of two independent lenses. Another lens is verifying the same items separately. Your
value is that you start COLD — you do not inherit the orchestrator's framing. Where you disagree
with the claims inlined below, say so loudly and cite `file:line`.

## The house evidence bar (this repo's own laws — follow them)

Read `AGENTS.md` first. It is long; the sections that bind you here:

- **An uncited claim is discarded.** Every finding needs `file:line`. Cite the SYMBOL, not just a
  line number — anchors in this repo have drifted 14-500 lines while still "resolving".
- **A check that passes in BOTH arms is broken.** For every fix you propose, state the
  **bidirectional oracle**: what the test shows on the PRE-FIX baseline (it MUST fail there) and
  what it shows after. A test that cannot fail is not evidence.
- **A grep zero is UNRESOLVED, not ABSENT.** If you report "0 occurrences", also report a positive
  control proving your pattern can match something. Six greps returned false numbers in one recent
  session here.
- **Do not add a population member by reasoning it is covered — call it.** A recent enumerating test
  had its population wrong three times in one day, every time via "builder A transitively covers
  builder B".
- **A producer is not a presenter** — code that STAMPS a field is not the code that must DISCLOSE it.

## Context: what the orchestrator believes is true (validate, do not re-derive)

The live queue is `docs/TASK_BOARD.md` (NOT `docs/BACKLOG.md`, which is a 1244-line historical
ledger). Live PyPI is **v1.101.27**. These items are listed OPEN:

**P1**
- **#15 MaxSim doc-honesty.** Claim: `find_command`'s docstring advertises MaxSim late-rerank, but
  the only control is an undocumented `TG_LATE_RERANK=1`; and `tests/unit/test_find_command.py`
  claims to prove the stage "observably reorders" while asserting only `exit_code == 0` / non-empty /
  no-exception — all three hold with the env UNSET. Its stub already inverts the ranking, so a
  one-line ORDER assertion would have a genuine red arm.
- **#22 GPU exit-2 calibration.** Claimed BLOCKED on a contract decision. Do not propose code here;
  tell us whether the CONTRACT question ("exit 2 means INCOMPLETE, but that search completed") has a
  defensible answer.

**P2**
- **`--quiet` silently dropped by both internal rg-passthrough branches** in `cli/main.py` (claimed
  ~`:7937-7943` plain and ~`:8004-8017` stats), because
  `backends/ripgrep_backend.py::_build_cmd` never translates `config.quiet` into rg's `-q`.
  **CAUTION:** commit `cfc3264` ("fix(search): -q belongs ONLY on the streaming consumer -- it was
  causing a FALSE ZERO") recently touched this exact area. Determine whether that commit fixed,
  partially fixed, or complicated this item. This is the single most important thing you will check —
  a naive "add `-q` to the rg argv" fix may re-introduce the FALSE ZERO that commit fixed.
- **`--ndjson` zero-match discloses nothing in-band in EITHER engine.**
- **The three `main.rs` envelope literals have no direct test**, and the CUDA one is `cargo check`-ed
  but never `cargo test`-ed (`cuda-feature-check` in `.github/workflows/ci.yml`).
- **`AGENTS.md:1437` is stale on the argv sweep** — certifies a sweep as complete that PR #872
  reopened.

**P4 carried**: #58 promote `tg route-test` hidden->public; #98 MCP tool consolidation; #141 native
AstBackend vs ast-grep DSL divergence; #160 dogfood feature tail; #115 symlink sweep (3 unguarded
`std::fs::write`); #125 checkpoint `except Exception`->`except BaseException`; #143/#155 Opus-gate
LOW follow-ups; dead code `sidecar.py::_classify_lines`.

**A CONTRADICTION to resolve against the code:** `docs/TASK_BOARD.md` lists #115 and #125 as OPEN,
while `docs/BACKLOG.md` (the 2026-07-31 audit table) marks both **KILLED / "Mark CLOSED"**. One of
those documents is wrong. Decide which, from the code.

**Also open from the 2026-07-31 deep-dive** (`docs/audits/2026-07-31-tensor-grep-deep-dive.md`,
remediation plan at `docs/superpowers/plans/2026-07-31-tensor-grep-audit-remediation.md` — read
both): #858 `codemap._atomic_write_text` symlink-dest replacement; #859 Form-1 writer ratchet
missing; #860 disclosure-docstring lie + CONTRACTS Slice-2 lie; **#865 `--ltl` accepted in bootstrap
but absent from `rust_core` clap (clap-reject on delegation — potentially a live user-facing break)**;
#863 daemon tokenless `is_authorized` fail-open; #864 dash-named relative `$file`.

## Hard constraints on what you may propose

- **DO NOT reopen settled battles.** These are RETIRED with receipts and re-proposing one is a
  documented failure: GPU-for-search crossover, cAST structural chunking, dense int8/binary/PCA
  embedding compression, free-threading, "beat rg on cold search", the `HashSet<PathBuf>` distinct-path
  counter, renaming `incomplete_paths_count`, `SearchStats::is_empty()` as a live bug. See the RETIRED
  table at the bottom of `docs/TASK_BOARD.md`.
- **CEO-gated, do not plan work for:** #72 benchmark publication, #131/#169 GPU rebuild, #48 native
  front door, #77 ledger scope.
- **Env-blocked (needs a Linux/WSL box):** #89, #90, #109.
- **This dev box is a SHARED SERVER.** Do not run `cargo build`/`test`/`check`/`clippy`, do not run
  `tests/e2e/test_routing_parity.py` (it invokes `cargo run`), and do not run benchmark harnesses.
  Reading Rust source is fine; compiling it is not.
- Adding a `tg` command or a search flag has **four registration sites** (and a new search flag has
  two front doors). Miss one and it silently misroutes to ripgrep. See `AGENTS.md`, "Adding a Command
  or Flag". Any proposal touching a flag MUST enumerate the sites it needs to touch.
- A new MCP tool is a **5th** registration site and requires bumping `_TG_MCP_SERVER_CONTRACT_VERSION`.

## What to produce

Write `docs/audits/2026-08-01-codex-backlog-campaign-plan.md` with:

### Part 1 — Verification table
| item | verdict (CONFIRMED-OPEN / ALREADY-FIXED / REFUTED / CHANGED) | key `file:line` | note |

One row per item above. A verdict with no citation is discarded — say `UNVERIFIED` instead and
explain what blocked you.

### Part 2 — Ranked implementation plan
Ranked by (user-facing harm x confidence) / risk. For EACH item you recommend building:

- **What breaks today**, with the concrete invocation that reproduces it.
- **The fix**, at symbol granularity (not line numbers — they drift).
- **The bidirectional oracle**: the exact test, what it does on the PRE-FIX baseline (must be RED),
  what it does after (GREEN). If you cannot describe a red arm, say so — that item is not ready.
- **Registration sites touched** (if any flag/command/MCP surface is involved).
- **Blast radius**: which other commands/routes share the symbol you are changing.
- **Release class**: `fix:`/`feat:` (RELEASES — one merge per publish) vs `docs:`/`test:`/`chore:`
  (batchable).

### Part 3 — What you would NOT build, and why
Items you judge to be non-defects, already fixed, or not worth the risk. A documented retirement is
worth as much as a fix here. Be specific about what killed each one.

### Part 4 — Disagreements with the orchestrator
Anything above that you found to be wrong. This section is the highest-value part of your report.

## Constraints

- Do NOT modify code. Do NOT `git add`/`commit`/`push`. Report only.
- Do NOT compile Rust or run the e2e/benchmark suites (shared server).
- Use model `gpt-5.6-sol` end-to-end; do not switch models mid-session.
- Working dir is `C:\dev\projects\tensor-grep`.
- For large files, focus via `git log -p -- <file>` on the relevant region rather than reading whole.
- Do NOT continue to interactive mode.

## Done criteria

Print exactly:
`[codex-audit] DONE backlog-campaign confirmed=N refuted=N already-fixed=N build=N file=docs/audits/2026-08-01-codex-backlog-campaign-plan.md`
Then exit.
