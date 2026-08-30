# tensor-grep — Orchestrator Audit Backlog

> **Scope:** SESSION START deep-dive (2026-08-30 **pass 5**). Discovery only — no feature code.
>
> **Relationship to repo trackers:** `docs/BACKLOG.md` = historical ledger;
> `docs/TASK_BOARD.md` = operational one-pager (`2026-08-30.1`). **This file** = orchestrator
> synthesis for cost-tiered SDLC — refresh each SESSION START.

| Field | Value |
|---|---|
| HEAD | `3b6145b1a72ffb5a72392e6acbed8bc787b412aa` on `docs/docs-reconcile-2026-08-30` |
| `origin/main` | `6450923` — `chore(release): v1.113.6` |
| Branch | `docs/docs-reconcile-2026-08-30` (PR #1123) |
| Dirty / untracked | `.orchestrator/`, `.wayfinder/`, `backlog.md`, `docs/plans/ENV-SYNC.md`, `docs/audits/2026-08-29-ceo-backlog-update.md` |
| PyPI / pyproject | **1.113.6** on branch; local venv `tg` reports **1.113.5** |
| Published tag | **v1.113.6** on `origin/main` (4/4 artifacts per AGENTS handoff) |
| Open PRs | **1** — #1123 **READY_TO_MERGE** (CI `33295639980` **success** @ `3b6145b`) |
| Main CI | **success** run `33292669573` @ `a77a150` (SEC-001 merge; `6450923` is `[skip ci]`) |
| Worktrees | **1** — canonical checkout only |
| Tests (inventory) | **450** `test_*.py` — unit **409**, e2e **22**, integration **17**, eval **2** |

---

## Context chunk map (`tg inventory` — split guidance)

**Rule:** Never load `main.py` + `repo_map.py` in one subagent seat. One command group or language module per seat.

| Chunk | Path / files | tg inventory | LOC (key) | Agent brief |
|---|---|---:|---:|---|
| **A — CLI dispatch** | `cli/main.py`, `bootstrap.py`, `commands.py` | 79 files / 3.6 MB (whole `cli/`) | main **13,409**; bootstrap **1,701** | argv normalization, Typer, A90 routing, 4-site registration |
| **B — Symbol graph** | `cli/repo_map.py`, `lang_*.py`, `repo_map_lang_*` | (in cli chunk) | repo_map **15,216** | one `lang_*` module per seat; never whole file |
| **C — MCP surface** | `cli/mcp_server.py`, `mcp_*_tools.py` | (in cli chunk) | mcp_server **5,341** | LLM contract version, tool error sanitization |
| **D — Session / ledger** | `session_daemon.py`, `session_store.py`, `ledger_store.py`, `checkpoint_store.py` | (in cli chunk) | daemon **2,139** | IPC, pre-auth caps, checkpoint TOCTOU |
| **E — Backends** | `backends/*.py`, `core/pipeline.py` | 11 files / 249 KB | pipeline **449** | fail-closed contract, disposition ledger gap |
| **F — Retrieval / find** | `core/retrieval_*.py`, find CLI | 21 files / 176 KB (whole `core/`) | — | BM25/dense/RRF; ranking regressions |
| **G — Native FFI** | `rust_core/` | n/a (binary ~53 MB in tree) | — | boundary tests only; no full-file reads |
| **H — Whole package** | `src/tensor_grep` | **126 files / 57.0 MB** | — | orient/inventory before wide fan-out |

**Language tier (product):** `parser-backed-refs-callers:c-cpp-csharp-go-java-javascript-php-python-rust-typescript+foundational-defs-imports-only:` → **10 parser-backed / 0 foundational**.

---

## Baseline table (measured 2026-08-30 pass 5)

| Gate / probe | Command | Result | Notes |
|---|---|---|---|
| File-size ratchet | `python scripts/file_size_budget.py` | **PASS** | 907 files, 25 grandfathered, 0 regressions |
| Bare-call ratchet | `python scripts/bare_call_ratchet.py` | **PASS** | 3 modules, 0 bare calls |
| Split-floor measurement | `python scripts/measure_split_floor.py` | **BLOCKED** | main **7310** / repo_map **6719** / mcp **2506** lines LOCKED — **SPLIT CANNOT REACH 1500** (A130); agent_capsule **693** viable |
| Ruff lint | `ruff check .` | **PASS** | |
| Ruff format `--preview` | `ruff format --check --preview .` | **PASS** | 985 files (was 15 markdown FAIL) |
| Mypy | `mypy src/tensor_grep` | **PASS** | 121 files |
| Rust compile | `cargo check` (rust_core) | **PASS** | ~7s local |
| Governance pytest | skill sync + drift + public docs + routing parity | **PASS** | **120 passed** (~80s) |
| Task board freshness | `test_task_board_freshness.py` | **PASS** | 11 passed |
| Local `tg doctor` | `tg doctor --json` | **DEGRADED** | venv 1.113.5; stale-skipped native (expected dev box) |
| `pytest.skip` census | `rg -c pytest.skip tests/` | **138** skips | env-gated / optional-engine — not debt markers |
| `# TODO` in `src/` | `rg` | **0** | |

Raw artifacts: `.orchestrator/baseline-pass5-governance.txt` (this run)

---

## Audit buckets (pass 5)

| Bucket | Scope | Method |
|---|---|---|
| **A — CLI / routing / argv** | `bootstrap.py:385`, `bootstrap.py:436-441`, routing e2e | Tier-1 agent + `test_routing_parity.py` 120/120 in governance slice |
| **B — MCP / session / security** | `mcp_server.py`, `session_daemon.py`, `checkpoint_store.py` | Measured grep + handler-disposition ledger cross-check |
| **C — Backends / fail-closed** | `backends/*.py`, `core/pipeline.py` | 50× `except Exception`; 0 backend rows in disposition ledger |
| **D — Tests / governance** | ratchets, split-floor, monkeypatch coupling | scripts + 119 `cli_main` patch sites / 11 files |
| **E — Architecture / scale** | giant modules, DI prerequisite | `measure_split_floor.py` + line census |

---

## P0 — Environment / in-flight work

| ID | Severity | Finding | Evidence | Action |
|---|---|---|---|---|
| ENV-001 | MEDIUM | Local venv behind tag | `tg --version` → **1.113.6**; `pyproject.toml` → **1.113.6** | **CLOSED** (2026-08-30) |
| ENV-002 | LOW | Doctor stale-skipped native | dev box without in-tree `tg.exe` | Expected; not a product bug |
| DOCS-RECONCILE | — | **SHIPPED** | PR #1123 @ `e6ba187` | Merged 2026-08-30 |
| HYGIENE-FORMAT | — | **RETIRED** | Premise falsified: 15/15 blobs pass ruff via stdin @ `e6ba187` | Do not merge hollow branch |
| HANDLER-CENSUS-W2 | — | **WAVE 1 READY** | 47 backend + 58 MCP `str(exc)` census | Open `docs:` PR |
| SEC-001 | — | **SHIPPED on main** | #1122 @ `a77a150` → release **v1.113.6** | Verify published wheel after next `uv sync` |

---

## P1 — Security (verified seams, no build started)

| ID | Severity | Finding | Evidence | Suggested fix |
|---|---|---|---|---|
| SEC-002 | HIGH | Pre-auth daemon thread exhaustion | `session_daemon.py:1738` `ThreadingMixIn` per accept (A121) | Bounded pre-auth worker pool / semaphore (DD-006-PERF) |
| SEC-003 | HIGH | Checkpoint ancestor TOCTOU | `checkpoint_store.py` resolve-then-copy pattern | Directory-handle anchoring + RED |
| SEC-004 | HIGH | Unbounded checkpoint metadata reads | **8** `.read_text()` @ `:384,:434,:513,:526,:578,:589,:1157,:1213` | Byte-cap before parse |
| SEC-005 | HIGH | Windows daemon token ACL fails open | `session_daemon.py:294-325` | Fail closed on `icacls` failure |
| SEC-006 | HIGH | Raw `str(exc)` on daemon IPC | `session_daemon.py:637`, `:2025` | Sanitized error codes |
| SEC-007 | HIGH | MCP tools leak `str(exc)` | **58** hits in `mcp_server.py` | Standardize `_sanitized_tool_error` |
| SEC-008 | HIGH | `refresh_on_stale` masks any exception | `session_daemon.py:1947` broad `except Exception` | Catch explicit stale signals only |
| SEC-009 | HIGH | Opt-in validation `--` sentinel retired | `apply_policy.py` documented retirement | Re-open only if untrusted fragments accepted |
| SEC-010 | MEDIUM | Audit manifest recording fails open | `mcp_server.py:1065-1072` | Verify all mutation tools |
| SEC-011 | MEDIUM | Authenticated `stop` enables local DoS | `session_daemon.py:1874-1878` | Restrict to parent PID |
| SEC-012 | MEDIUM | Invalid `TG_MCP_ROOT` → cwd fallback | `mcp_server.py:1348-1366` | Fail closed when env set but invalid |

---

## P1 — Architectural / contract debt

| ID | Severity | Finding | Evidence | Suggested fix |
|---|---|---|---|---|
| ARCH-001 | HIGH | Giant CLI modules locked by test patches | split-floor: **62+106+28** functions LOCKED | DI campaign or honest ratchet exception (A130) |
| ARCH-002 | HIGH | **47** backend `except Exception` unledgered | census JSON @ `docs/audits/2026-08-30-handler-census-w2-backends.json`; ledger **0** backend rows | HANDLER-CENSUS-W2 wave 2 |
| ARCH-003 | MEDIUM | Reserved command + positional → search (A90 gap) | `bootstrap.py:436-441` refuses only when flag follows; `edit-ready target.txt` searches | Product decision: refuse vs document |
| ARCH-004 | MEDIUM | `main.main_entry()` secondary door | `main.py:13396+` may bypass A90 | Mirror refusal or document test-only entry |
| ARCH-005 | MEDIUM | `worker` not in `PUBLIC_TOP_LEVEL_COMMANDS` | `commands.py:25` vs parity list | 4-site registration |
| ARCH-006 | MEDIUM | Bootstrap vs Rust flag-list drift | `_TG_ONLY_SEARCH_FLAGS` vs native list | Registry parity test (A83) |
| ARCH-007 | MEDIUM | **11** `RuntimeError` in backends | may bypass `BackendExecutionError` | Raise contract error + tests |
| ARCH-008 | MEDIUM | Backend degrades without `fallback_reason` | pipeline / optional backends | Stamp visible degrade |
| ARCH-009 | MEDIUM | `ast_workflows.py` batch failure mask | `:906-916` | Surface per-rule errors |
| ARCH-010 | LOW | Governance docs unbounded | `AGENTS.md` ~368 KB, `docs/BACKLOG.md` ~331 KB | Doc-size / receipt-age pass (no gate today) |
| ARCH-011 | LOW | **119** `monkeypatch.setattr(cli_main` sites / **11** files | highest coupling: `test_cli_modes_cli_json.py` | Split-floor / DI prerequisite |
| ARCH-012 | LOW | Subprocess diagnostic trap | **45** `capture_output=True`+`check=True` in scripts/tests | Audit-only (AGENTS.md class) |

---

## P2 — Product / platform (canonical board — not re-litigated)

Source: `docs/TASK_BOARD.md` index **`2026-08-30.1`**.

| ID | Row | Disposition |
|---|---|---|
| PROG-089 | #89 WSL path-domain | **BLOCKED** |
| PROG-090 | #90 WSL scan false-clear | **BLOCKED** |
| PROG-F5 | F5 edit-ready steps 3–5 | **BLOCKED** |
| PROG-F6 | F6 edit-verification | **BLOCKED** |
| PROG-F8 | F8 workspace | **BLOCKED** |
| PROG-MCP | MCP-SURFACE | **BLOCKED** (Python-sequenced; contract **1.7.0**) |
| CEO-072 | #72 benchmark claim | **CEO_GATED** |
| CEO-131-169 | GPU publish / proof | **CEO_GATED** |
| DEMAND-DD006 | DD-006 daemon perf | **DEMAND_GATED** — design #1015 on main; product build not started (A122) |
| DEMAND-TOCTOU | RUST-REPLACE-TOCTOU | **DEMAND_GATED** |
| DEMAND-255 | #255 many-pattern dedup | **DEMAND_GATED** |
| DEMAND-AST | AST-DSL-PARITY | **DEMAND_GATED** |
| DEMAND-MCP-LEAN | MCP-LEAN-DEFAULT | **DEMAND_GATED** |
| DEMAND-REFRESH | CONTINUOUS-REFRESH | **DEMAND_GATED** |
| SHIPPED | RUST-REPLACE-SYMLINK, F7, CPU-BACKEND, REF-CALL-REGISTRY, … | **SHIPPED** (see board) |

**Closed-world (board):** 28 canonical rows / **17 unfinished** = 0 READY, **6 BLOCKED**, 0 IN_FLIGHT, **5 CEO_GATED**, **6 DEMAND_GATED** (+ SHIPPED/RETIRED).

---

## P3 — Hygiene

| ID | Finding | Action |
|---|---|---|
| HYGIENE-001 | 15 markdown files fail local `ruff format --preview` | **RETIRED** — Windows CRLF false RED; blobs pass on `origin/main` |
| HYGIENE-001b | `*.md` not in `.gitattributes eol=lf` | Optional dev-hygiene follow-up |
| HYGIENE-002 | 138 `pytest.skip` in tests | **NO ACTION** — env/optional-engine gates |
| HYGIENE-003 | `# TODO` / `# FIXME` in `src/` | **0** actionable markers |
| HYGIENE-004 | `pytest-timeout` absent locally | Shell `timeout` per A6 for long runs |
| HYGIENE-005 | 45 subprocess capture+check anti-patterns | Audit-only |

---

## Missing contract coverage (consolidated)

| Area | Gap |
|---|---|
| Handler dispositions — backends | **50** broad handlers; ledger has **0** backend module rows |
| Handler dispositions — MCP `str(exc)` | **58** raw exception strings on wire |
| Checkpoint reads | **8** unbounded `read_text()` |
| Bootstrap A90 | `edit-ready <path>` still searches when no flag |
| Split-floor / 1500 LOC budget | **Cannot reach** for 3 giants without DI |
| Stock `tg scan --ruleset` | A125 — clean container without `ast` extra |
| Edit / verify-edit (S1) | Escrow + drift fingerprint not shipped |
| WSL #89/#90 | No CI arm without WSL runner |
| Full pytest matrix | Not run this pass (CPU-safe); CI is oracle |

---

## Dead code / retirement (do not re-charge)

- Automated **vulture** run: **NOT PERFORMED** (no installed dead-code oracle this pass).
- **54** repo files *mention* "vulture" or "dead code" in prose/tests — not a census.
- Per `docs/TASK_BOARD.md` **RETIRED:** cAST default chunking, dense int8, GPU-for-search crossover, F10 late-rerank, #48 native-front-door rewrite, etc.

---

## Common-sense-check (promotion gate — pass 5)

| Finding | Promoted? | Reason |
|---|---|---|
| 47 backend handlers unledgered | **YES** | JSON census @ `e6ba187`; ledger has 0 backend rows |
| 58 MCP `str(exc)` leaks | **YES** | Measured count; security wire surface |
| 8 unbounded checkpoint reads | **YES** | Enumerated line numbers |
| Split-floor BLOCKED | **YES** | Script output verbatim; A130 |
| 15 format failures (local) | **RETIRED** | Blobs pass stdin probe; disk CRLF only |
| A90 `edit-ready` positional gap | **YES (medium)** | Agent cited `bootstrap.py:436-441` + `:385` |
| 119 monkeypatch sites | **YES (architecture)** | Measured; split prerequisite |
| 138 pytest skips as debt | **NO** | Intentional env gates |
| 54 "dead code" prose hits | **NO** | Not a dead-code census |
| Routing parity broken | **NO** | 120/120 governance slice green |
| SEC-001 not shipped | **NO** | On main @ `a77a150`; release tag v1.113.6 |

---

## Recommended SESSION CONTINUE slices (ranked)

1. **HANDLER-CENSUS-W2** — **WAVE 1 READY** (audit receipt + JSON census; `docs:` PR)
2. ~~**HYGIENE-FORMAT**~~ — **RETIRED** (blob premise falsified)
3. ~~**ENV-RESYNC**~~ — **CLOSED** (venv already 1.113.6)
4. **HANDLER-CENSUS-W2 wave 2** — ledger append + MCP sanitize (build; A3 gate)
5. **SEC-002-SPEC** — DD-006 daemon cap design (demand-gated; needs CEO build go)
6. **ARCH-A90-DECISION** — product call on positional-after-reserved-command
7. **DD-006-PERF-BUILD** — CEO authorization required

---

*Pass 5 deep-dive 2026-08-30: tg inventory (126 files) + Tier-1 `agent --yolo` mechanical scans + 120/120 governance pytest + split-floor + disposition cross-check. $0 paid-model spend. No feature code.*
