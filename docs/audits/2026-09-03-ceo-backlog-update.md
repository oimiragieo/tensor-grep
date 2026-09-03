# CEO backlog update — 2026-09-03 (9:45 AM session)

**Spend:** `$0 / $0`; change: `$0`
**Phase:** CEO_UPDATE; item: HANDLER-CENSUS-W2-a — product harden applied to worktree, **uncommitted**, Sol pass-2 incomplete
**Next:** (1) Safe exact-path commit of W2-a harden; (2) Sol audit on *committed* tip outside Cursor; (3) `fix:` PR only after literal `AUDIT_CLEAR`; (4) merge → release

---

## Snapshot pins (re-derived 2026-09-03 09:45 ET)

| Pin | Value | How verified |
|---|---|---|
| Branch | `fix/handler-census-w2a-cpu-ripgrep` | `git branch --show-current` |
| `HEAD` | `ed740d09ea93e2dce800b990a2ac2df24b9ce8bf` | `git rev-parse HEAD` |
| `origin/main` | `ed740d09ea93e2dce800b990a2ac2df24b9ce8bf` | `git rev-parse origin/main` |
| HEAD == origin/main | **yes** — branch is at parity, dirty only | diff is uncommitted worktree changes |
| Open PRs | **0** | `gh pr list --state open` → `[]` |
| Main CI @ `ed740d0` | **2× completed/success** (`33365913839`, `33324743164`) | `gh run list --branch main --workflow=ci.yml` |
| Public product | **v1.113.6** — **4/4 PyPI files** | PyPI JSON verified prior packet |
| W2-a harden | **dirty, uncommitted** — `cpu_backend.py`, ledger JSON, test file, plan docs | `git status -sb` (6 modified, several untracked) |
| Sol pass-1 | **FIX-FIRST** at `cpu_backend.py:692` / `:764` (regex swallow) | `.orchestrator/w2a-sol-audit.txt` |
| Sol pass-2 | **INCOMPLETE** — session crashed before terminal verdict | `.orchestrator/w2a-sol-audit-pass2.txt` (no `AUDIT_CLEAR`/`FIX-FIRST:` line) |
| Spend | `$0` authorized / `$0` spent | Sol/Fable **FAILED A78** — not pending |

---

## Completed milestones (exact SHAs + verification)

| Slice | SHA / PR | Verification command | Release |
|---|---|---|---|
| **SEC-001** bootstrap `--` argv sentinel (CWE-88) | PR #1122 → `a77a150` | `gh pr view 1122 --json mergeCommit` | v1.113.6 (`6450923`) |
| **#48 retire** | PR #1121 → `8a879b2` | board `Status: RETIRED` | docs: |
| **DOCS-RECONCILE** board/handoff stamp | PR #1123 → `e6ba187` | `gh run view 33296804300 --json conclusion` | docs: |
| **HANDLER-CENSUS-W2 wave 1** (audit docs) | PR #1124 → **`ed740d0`** | `gh run view 33324743164 --json conclusion` → success | docs: |
| **HYGIENE-FORMAT** | RETIRED (stdin blob check green) | N/A | N/A |
| **ENV-001** | CLOSED (venv/doctor aligned) | `uv run --no-sync tg --version` → 1.113.6 | N/A |

### W2-a in-progress — not shipped

| Arm | State |
|---|---|
| 17 backend ledger rows (cpu+ripgrep) + `_EXPLICIT_AUDITED_MODULES` | Applied dirty — `docs/audits/2026-08-20-handler-dispositions.json`, `tests/unit/test_handler_dispositions.py` |
| `cpu_backend.py` decode-only harden (Sol FIX-FIRST response) | Applied dirty — `src/tensor_grep/backends/cpu_backend.py` |
| RED/GREEN test | **1 passed** (scoped) — `test_python_fallback_regex_engine_failure_is_not_clean_no_match` |
| Disposition gate | **11 passed** (prior run ~209s) |
| `ruff check` + `format --preview` | **clean** on touched files |
| Sol pass-2 verdict | **INCOMPLETE** — not clearance |
| Commit / PR | **not done** |

**Verification commands for W2-a (safe to run):**
```powershell
# Check dirty state
git -C C:\dev\projects\tensor-grep status -sb

# Scoped harden test only (< 2s)
cd C:\dev\projects\tensor-grep
uv run --no-sync python -m pytest tests/unit/test_cpu_backend.py::test_python_fallback_regex_engine_failure_is_not_clean_no_match -q

# When ready to commit (exact paths — NO git add -A):
git add src/tensor_grep/backends/cpu_backend.py tests/unit/test_cpu_backend.py tests/unit/test_handler_dispositions.py docs/audits/2026-08-20-handler-dispositions.json docs/plans/HANDLER-CENSUS-W2.md docs/plans/2026-08-30-handler-census-w2a-cpu-ripgrep.md docs/plans/2026-08-30-backlog-dependency-spine.md docs/audits/2026-09-03-ceo-backlog-update.md
git commit -F - <<MSG
fix(handlers): harden cpu_backend search cascade decode/search separation

Sol FIX-FIRST (HANDLER-CENSUS-W2-a): utf-8/latin-1 match-test arms
wrapped decode+search in one except Exception: pass, so a regex engine
failure became matched=False (silent no-match, Backend Fail-Closed
violation). Fix: decode-only inside try/except; regex_str.search runs
in else clause so engine failures propagate to the outer RuntimeError
wrapper (idx 9).

Sites: source_lines cascade (idxs 2/3, ~:691/:694) and streaming arm
(idxs 6/7, ~:766/:769). Ledger rows hardened_in=HANDLER-CENSUS-W2-a.
RED test: test_python_fallback_regex_engine_failure_is_not_clean_no_match.
MSG
```

---

## Canonical board — 100% unabridged (mechanical `Status:` census)

**Source:** `docs/TASK_BOARD.md` `## Canonical status index`  
**Re-derived 2026-09-03:** total=**29** | unfinished=**16** | 0 READY | 0 IN_FLIGHT (board) | **6 BLOCKED** | **4 CEO_GATED** | **6 DEMAND_GATED** | terminal: **8 SHIPPED** + **5 RETIRED**

> Orchestrator campaign work (W2-a) is **not** a board row — it is tracked in `.orchestrator/state.json`.

### BLOCKED (6) — cannot build without external unlock

| ID | Blocker receipt | AI-doable today? |
|---|---|---|
| **#89** | WSL path-domain `path_not_found` on `/mnt/c/...`; needs real WSL host; PR #966 attribution STALE (wrong scope, closed 2026-08-20) | Fresh plan + RED on real WSL host only |
| **#90** | WSL raw-path scan `matched_rules=0` vs translated control; same WSL CI gap | Same as #89 |
| **F5** | Edit-ready Steps 3–5 touch `rust_core/**` + `tests/e2e/**`; shared-box `cargo`/e2e ban → CI/cloud; Step 2 shipped #943 | After unblock plan / CI runner |
| **F6** | Mixed: Python/schema/evidence-signing slices **buildable** (S1 de-block); native verify-edit + e2e → CI/cloud; Step 0 shipped #939 | Python slices only — needs scoped plan + A3 |
| **F8** | Workspace program; `path_domain.rs` **absent** on `origin/main` (verified 2026-08-30); real touch = `main.rs` until design names workspace APIs | Design pass first |
| **MCP-SURFACE** | Task 4 sequenced after Task 2C; `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` at `mcp_server.py:188` (Python const only); plans 1.8.0→1.9.0 bump | After Task 2C contract plan |

### CEO_GATED (4) — CEO decision required

| ID | Blocker receipt | AI-doable today? |
|---|---|---|
| **#72** | CEO approval for a **new public benchmark claim** (no public surface to retract — premise correct) | CEO taste — no AI action |
| **#77** | CEO decision on ledger-enforcement scope (#77/F9) | CEO policy |
| **#131** | CEO decision on publishing GPU-flavor native assets | CEO + #169 |
| **#169** | **Only financial hard stop** — GPU proof infra / spend approval | CEO only |

### DEMAND_GATED (6) — no trigger / not authorized

| ID | Blocker receipt | AI-doable today? |
|---|---|---|
| **#255** | No named 100+-pattern user; council LEAVE 2026-08-14 | No until demand |
| **DD-006** | Demand SATISFIED (20-client probe); design #1015 merged; **product PERF+HONESTY build not authorized** (A122 — docs ≠ shipped) | **CEO build go required** |
| **AST-DSL-PARITY** | No perf-blocked consumer; ast-grep rewrite +22% faster; LEAVE 2026-08-14 | Defer |
| **MCP-LEAN-DEFAULT** | Spec-level OK; sequenced after Task 2C | Defer — after 2C |
| **CONTINUOUS-REFRESH** | Warm-index scoping only (no build); peers: TriSeek 0.4.2 | Scoping pass only |
| **RUST-REPLACE-TOCTOU** | Residual races documented; characterization pin NOT inverted (acceptance signal unmet) | After demand/pin flip |

### SHIPPED (8)

| ID | PR(s) | Merged SHA | Release |
|---|---|---|---|
| #36 | #903 | — | v1.x |
| #37 | #908 | — | v1.x |
| #109 | #605 | — | v1.x |
| #859 | #913, #918, #920 | `211d850c` | v1.x |
| F7 | #950 #952 #955 #957 #963 | `9f854d49` | v1.x |
| CPU-BACKEND | #923 #925 #963 | `f29c9484` | v1.x |
| REF-CALL-REGISTRY | #915 #940 #963 | `3dbe85b1` | v1.x |
| RUST-REPLACE-SYMLINK | #1010 | `d31a051f` | v1.110.16 |

### RETIRED (5)

| ID | Reason |
|---|---|
| #22 | Exit code semantics resolved |
| F2 | Anonymous-agent sentinel deliberately retained |
| **#48** | GitHub issue closed "not planned" 2026-08-24; 5/5 council verdict |
| F10 | MaxSim ndcg@10 0.068 vs 0.305 RRF; no `tg` command path |
| DD-004 | INFO/WEAKENED RuntimeError is not empty-success; banked in AGENTS.md |

---

## Validated lessons (this window, 2026-08-30 → 2026-09-03)

1. **Long Sol + slow disposition pytest crashes Cursor.** For fragile sessions: short probes only. Run Sol outside Cursor after a small commit — not inside a live AI session.
2. **"Ledger-only" waves can require `fix:` product code.** An INTENTIONAL-BOUNDARY row that lies about a silent regex→no-match path is a Backend Fail-Closed violation, not a documentation nit.
3. **Incomplete Sol log ≠ AUDIT_CLEAR.** Require the literal terminal verdict line. Absence = UNRESOLVED.
4. **Decode failures and search failures are different contracts.** `except Exception: pass` that wraps both decode AND `regex_str.search` together silently converts engine errors into "unmatched line." Separate them.
5. **Mechanical `Status:` census beats prose.** Board is 29/16/4-CEO-GATED. Paragraphs that still count #48 as CEO_GATED are stale; the checklist wins.
6. **A78 = FAILED seat**, not pending. Sol/Fable quota expiry is recorded as FAILED and does not become durable clearance when a substitute is used.
7. **`_compile_regexes` mock is the right seam** for testing regex-engine failure isolation. `patch.object(CPUBackend, "_compile_regexes", staticmethod(...))` reaches both the source_lines and streaming topology arms.

---

## Features / enhancements that would improve the tool/workflow

### Highest value (blocked by nothing except decision)

1. **Cursor-safe orchestration discipline** — hard ceiling: no multi-minute Sol seats inside Cursor; write prompt to file → run `codex exec` from a separate terminal after a commit. Would have prevented two session crashes.

2. **Fast disposition-suite subset** — split `test_handler_dispositions.py` into a 5s "schema/vocab" subset and a full locatability scan. CEO packets and pre-commit can run the fast subset without the 3-minute AST walk.

3. **`.gitattributes` `eol=lf` for `*.md`** — permanent fix for the Windows CRLF `ruff format --preview` false-RED that killed HYGIENE-FORMAT and costs ~5 min per misdiagnosis.

4. **Sol pass-2 committed-tip protocol** — after every `fix:` harden commit, a one-command `codex exec --model gpt-5.6-sol -C <repo> "$(cat .orchestrator/sol-prompt.txt)"` run from PowerShell (not inside Cursor) with a timeout. Verdict written to file; session reads tail only.

### Medium value (CEO go or demand trigger needed)

5. **DD-006-PERF product build** — concurrent session daemon has measured timeouts; design is merged (#1015). Only needs CEO "build go" + TDD + A3.

6. **F6 Python/schema/evidence-signing slices** — buildable-first (S1 de-block); Python half doesn't touch `rust_core`; would close part of the edit-verification gap without CI unblock.

7. **BACKLOG.md / AGENTS.md size gate** — both are >300 KB with no ratchet (BACKLOG.md 331 KB, AGENTS.md 368 KB as of 2026-08-23). A `scripts/file_size_budget.py` entry for each, or an age-out script moving dated receipts to `docs/audits/`, would prevent silent truncation for future agents.

8. **Lean MCP default** — MCP spec now codifies progressive discovery; up to 85% token reduction documented. Sequenced after Task 2C but design is clear.

### Lower priority / research

9. **CONTINUOUS-REFRESH scoping pass** — warm-index daemon multi-repo shape; peers shipped (TriSeek 0.4.2). No build yet, just a scoping doc.

10. **RUST-REPLACE-TOCTOU** — residual races in `replace_in_place`; machinery exists in `safe_write.rs`. Demand trigger = characterization pin inverting.

---

## Memory update (HITL — not auto-applied)

Written to: `C:\dev\projects\gotcontext-memory-private\docs\LESSONS_2026-09-03-tensor-grep-ceo-update.md`  
Index row added: `2026-09-03 | tensor-grep CEO UPDATE W2-a mid-harden | PENDING`  
State file updated: `.orchestrator/state.json`
