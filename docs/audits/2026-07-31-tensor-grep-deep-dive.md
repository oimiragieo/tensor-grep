# Tensor-grep Deep-Dive Audit Register — 2026-07-31

> Wave-1: four parallel lenses (security, fail-closed, MCP/trust, CI/arch).
> Wave-2: adversarial falsification (parent re-derive + parallel seats).
> Checkout audited: **v1.101.20** (`9d3dbdf`). Live tip at audit time: **v1.101.22**.

## Executive verdict

**Core security hardening holds.** MCP CWE-88 rewrite/index builders, checkpoint symlink walks,
daemon pre-auth bounds, `_index_lock.atomic_write_bytes`, and backend empty-on-exception fail-closed
are clean. Live work is (1) the still-open **#858–#863** queue with #861 partially superseded,
(2) **docs/BACKLOG rot** asserting fixed behavior as broken, and (3) a small set of **NEW** residuals
(validation-argv CWE-88, native `--ltl` dual-path, tip-stamp drift).

Do **not** reopen: #276 incompleteness campaign, GPU HOLD, cAST default, free-threading, MCP
`mcp>=1.27.2,<2` major cap.

---

## Prior-audit disposition

| Item | Status | Evidence |
|------|--------|----------|
| MCP `_build_rewrite_command` / `_build_index_search_command` `--` | **FIXED** | `mcp_server.py:1375`, `:1387` |
| Native / rg / ast-grep path `--` | **FIXED** | `main.py:3799`, `ripgrep_backend.py:870`, `ast_wrapper_backend.py` |
| Checkpoint `followlinks=False` / `follow_symlinks=False` | **FIXED** | `checkpoint_store.py:638`, `:893`, `:1390`, `:1445` |
| Daemon bounded pre-auth read + timeout | **FIXED** | `session_daemon.py:1773-1798` |
| Backend empty-`SearchResult` on exception | **FIXED** | No live swallow in `backends/` |
| AST metavar silent native misroute | **FIXED** (native-shaped fallback deliberate) | `pipeline.py:52-60`, `:230-233` |
| #276 JSON incompleteness | **CLOSED** | BACKLOG header |
| Rust #115 symlink writes | **CLOSED in product** | `write_bytes_refuse_symlink` at `main.rs:10234`, `:10262`, `:10891`, `:11031`; CHANGELOG closes #115/#125a |
| MCP contract `1.7.0` + major cap | **CLEAN** | `mcp_server.py:138`; `mcp>=1.27.2,<2` |
| Ledger Slice 2 git-root canonicalize | **CLOSED in code** | `ledger_store.py:48-57`, `:1198`, `:1335` |
| Inventory leading truncation banner | **CLOSED in code** | `inventory.py:372-376` |
| Mermaid visible incomplete node | **CLOSED in code** | `main.py:12655-12668` + pin test |

---

## Finding cards

### DD-858 / #858 — codemap atomic write bypasses symlink refuse

```yaml
id: DD-858
severity: LOW
title: tg codemap _atomic_write_text replaces symlink dest
mechanism: >
  Hand-rolled writer write_text(tmp)+replace_with_retry with no is_symlink precheck,
  no O_EXCL|O_NOFOLLOW, no mode-at-create. Shared atomic_write_bytes refuses symlink dests;
  codemap bypasses it. Bidirectional: replaces the link entry (target content intact —
  integrity / doc-generation surface, not classic RCE). Wave-2 WEAKENED severity HIGH→LOW.
evidence: src/tensor_grep/cli/codemap.py:801-812; callers :1234, :1292, :1299
wave2: VERIFIED (severity WEAKENED HIGH→LOW)
false_positive_check: >
  Confirm atomic_write_bytes still refuses symlink (yes). Confirm codemap does not call it (yes).
owasp_or_rule_of_two: A03/integrity; ASI tool misuse on write surface
already_tracked: "#858"
recommendation: >
  Route through _index_lock.atomic_write_bytes; TDD pin symlink dest refusal; keep security gate
  for write-surface hygiene even at LOW.
```

### DD-859 / #859 — no Form-1 writer ratchet

```yaml
id: DD-859
severity: MEDIUM
title: No AST ratchet forcing cli/ publish sites through atomic_write_bytes
mechanism: >
  #858 was invisible after enumeration-only #211/#665 because nothing Form-1-fails when a new
  hand-rolled writer appears. Need ratchet that reports non-zero on pre-fix codemap.py.
evidence: docs/BACKLOG.md Ready-to-build #859; codemap.py:801-812 as positive control
wave2: VERIFIED
false_positive_check: Ratchet must bite pre-fix blob (Form-1), not only post-fix green.
owasp_or_rule_of_two: n/a (process gate)
already_tracked: "#859"
recommendation: Ship with or immediately after #858.
```

### DD-861 / #861 — disclosure class residuals (narrowed)

```yaml
id: DD-861
severity: INFO
title: Codemap shared-banner unify is cosmetic; position bugs already fixed
mechanism: >
  Wave-2 KILLED inventory-trailing and mermaid-%%-only arms. Codemap already LEADS with
  PARTIAL: and folds all incompleteness causes into top-level partial (exit-2 aligned).
  Residual vs shared _emit_scan_incompleteness_banner is vocabulary hygiene only.
  Stale comments at main.py:8718-8723 / :14750-14751 fold into #860.
evidence: inventory.py:372-376; main.py:12655-12668; main.py:9147-9148; codemap.py:1124
wave2: WEAKENED → INFO (kill as position/class bug)
false_positive_check: Inventory prepend + mermaid visible node must stay.
owasp_or_rule_of_two: n/a
already_tracked: "#861"
recommendation: Close #861 as product gap; fold comment/docstring cleanup into #860.
```

### DD-860 / #860 — docs/docstring/tip stamp drift

```yaml
id: DD-860
severity: LOW
title: Stale disclosure docstring + release_docs_current_tag lag tip
mechanism: >
  _completeness_caveat_lines docstring claims map/context/edit-plan/agent exit 2 with no text —
  census shows banners wired. AGENTS/CONTRACTS/SESSION_HANDOFF stamp v1.101.20 while
  origin/main+PyPI are v1.101.22.
evidence: main.py:11509-11514; AGENTS.md release_docs_current_tag; origin/main 23e1f6f
wave2: VERIFIED
false_positive_check: Grep _emit_scan_incompleteness_banner call sites — non-empty.
owasp_or_rule_of_two: n/a
already_tracked: "#860"
recommendation: Non-releasing docs: PR reconcile prose + stamp to live tip; pin SOURCE beside claim.
```

### DD-862 / #862 — GPU evidence argv missing `--`

```yaml
id: DD-862
severity: LOW
title: agent_capsule GPU evidence_path appended without -- sentinel
mechanism: >
  evidence_command builds flags then append(evidence_path) with no --. Paths are normally
  resolve()-absolute / wslpath-translated, so practical CWE-88 is low; defense-in-depth only.
evidence: agent_capsule.py:1711-1721
wave2: VERIFIED (code) / WEAKENED (exploitability)
false_positive_check: Absolute POSIX/Windows paths cannot start with single - as flag.
owasp_or_rule_of_two: CWE-88 / MCP-276 class (CLI self-argv)
already_tracked: "#862"
recommendation: Insert -- before evidence_path; one unit test with dash-prefixed relative path.
```

### DD-863 / #863 — daemon tokenless fail-open

```yaml
id: DD-863
severity: LOW
title: session_daemon is_authorized returns True when token empty
mechanism: >
  if not self.token: return True. Production generates a token; test pins tokenless compat.
  Pre-auth DoS bounds remain solid — this is auth residual, not read DoS.
evidence: session_daemon.py:1765-1766
wave2: VERIFIED
false_positive_check: Confirm production start path always sets non-empty token.
owasp_or_rule_of_two: A07 / excessive agency if tokenless daemon exposed
already_tracked: "#863"
recommendation: Require token OR document deliberate bootstrap trust boundary in CONTRACTS.md.
```

### DD-001 — validation / policy argv without `--`

```yaml
id: DD-001
severity: LOW
title: apply_policy and Rust run_validation_command lack -- before remaining args
mechanism: >
  argv = [resolved_exec, *argv[1:]] with no sentinel. Rust $file substitution is absolute
  (dash-named files cannot lead as flags). Python _policy_file_arg prefers relative paths —
  a root file literally named -e can become argv -e after shlex (narrow, multi-precondition).
  MCP lint/test cmds default-OFF behind TG_MCP_ALLOW_VALIDATION_COMMANDS.
evidence: apply_policy.py:492-495, :707-720; main.rs:10616-10618, :10681-10690; mcp_server.py:852-862
wave2: WEAKENED (MED→LOW)
false_positive_check: MCP gated YES; Rust absolute YES; Python relative -e YES (narrow).
owasp_or_rule_of_two: CWE-88
already_tracked: "#864 (NEW)"
recommendation: >
  Insert -- before substituted $file in Python+Rust; pin -e-named edited file. Security gate optional at LOW.
```

### DD-002 — audit_manifest cross-process symlink TOCTOU

```yaml
id: DD-002
severity: LOW
title: Stale comment claims Rust audit-manifest write lacks O_NOFOLLOW
mechanism: >
  mcp_server.py:1743-1752 documents Part C as open. Wave-2 re-derive: write_audit_manifest_for_plan
  already calls write_bytes_refuse_symlink (main.rs:11031). Comment + line refs are STALE.
  Residual risk is only if some other write path bypasses that helper (not found for audit manifest).
evidence: mcp_server.py:1743-1752; main.rs:10940-11031; safe_write.rs:30+
wave2: KILLED (as open product gap) / VERIFIED (as docs-comment rot)
false_positive_check: Confirm write_audit_manifest_for_plan still ends in write_bytes_refuse_symlink.
owasp_or_rule_of_two: n/a (comment hygiene)
already_tracked: none (NEW docs rot)
recommendation: Update mcp_server comment to say Part C closed via write_bytes_refuse_symlink; cite symbol.
```

### DD-003 — CONTRACTS.md Slice-2 path-literal lie

```yaml
id: DD-003
severity: MEDIUM
title: CONTRACTS.md asserts Slice 2 record/find are path-literal; code canonicalizes
mechanism: >
  CONTRACTS.md:239 and :252 say record/find do NOT canonicalize to .git ancestor. ledger_store
  module note :48-57 and record/find entry points :1198/:1335 use _ledger_physical_root.
  Highest-cost docs shape: readers treat misses as expected and recompute / file false bugs.
evidence: docs/CONTRACTS.md:239, :252; ledger_store.py:48-57, :1198, :1335
wave2: VERIFIED
false_positive_check: Live tg ledger record . then find from subtree must hit same store.
owasp_or_rule_of_two: n/a (honesty)
already_tracked: none (NEW) — fold into #860 docs reconcile
recommendation: Rewrite CONTRACTS §9/§10 Slice-2 PATH prose to match code; grep skills/TASK_BOARD.
```

### DD-004 — cpu_backend RuntimeError hygiene

```yaml
id: DD-004
severity: INFO
title: cpu_backend raises RuntimeError not BackendExecutionError
mechanism: >
  Loud re-raise in search loop (not empty success). Contract hygiene only.
evidence: cpu_backend.py:770-771; main.py:8180-8188
wave2: WEAKENED → INFO
false_positive_check: Confirm no empty SearchResult on that path.
owasp_or_rule_of_two: n/a
already_tracked: none
recommendation: Optional wrap as BackendExecutionError for uniform CPU fallback.
```

### DD-005 — `--stats` platform route divergence

```yaml
id: DD-005
severity: MEDIUM
title: tg search --stats defaulted-scope note diverges Win vs Linux
mechanism: >
  On Windows --stats emits own stats and returns without is_empty branch; Linux reaches note.
  Strict xfail hides the fork. Latent Windows-only behavior gap.
evidence: docs/BACKLOG.md OPEN FINDINGS F1 follow-up (lines ~747-755)
wave2: VERIFIED (documented; not re-dogfooded both OS this pass)
false_positive_check: Structural route trace of --stats on both front doors.
owasp_or_rule_of_two: n/a
already_tracked: BACKLOG open finding (unnumbered)
recommendation: Root-cause route fork; unify before relying on defaulted-scope note.
```

### DD-006 — daemon worker semaphore #128c

```yaml
id: DD-006
severity: INFO
title: TG_DAEMON_MAX_WORKERS / worker semaphore never built
mechanism: >
  No semaphore/max-workers symbol in session_daemon.py. Not on live Ready queue.
evidence: docs/BACKLOG.md verify-flagged #128c; session_daemon.py (no match)
wave2: VERIFIED (unbuilt) / not a defect until DoS demand
false_positive_check: Grep TG_DAEMON_MAX_WORKERS — absent.
owasp_or_rule_of_two: n/a
already_tracked: "#128c (verify-flagged)"
recommendation: Leave banked; build only under measured daemon DoS demand.
```

### DD-007 / #115+#125 BACKLOG rot

```yaml
id: DD-007
severity: LOW
title: BACKLOG still lists #115/#125 as open LOW; product closed
mechanism: >
  CHANGELOG closes #115/#125a; Rust sites route through write_bytes_refuse_symlink.
  Re-opening would waste a cycle.
evidence: CHANGELOG.md ~15422; main.rs:10234, :10262, :10891; BACKLOG.md:971-975
wave2: VERIFIED (tracker rot)
false_positive_check: Grep std::fs::write at former sites — should be helper.
owasp_or_rule_of_two: n/a
already_tracked: "#115/#125 (CLOSE in tracker)"
recommendation: Mark CLOSED in BACKLOG LOW section with CHANGELOG cite.
```

### DD-008 — native `--ltl` dual-path hole

```yaml
id: DD-008
severity: MEDIUM
title: --ltl in bootstrap._TG_ONLY_SEARCH_FLAGS but absent from rust_core
mechanism: >
  Bootstrap forces Python for --ltl; native has zero ltl matches → clap-reject, not silent
  rg-passthrough. Loud dual-path hole vs --rank/--bm25/--semantic which ARE on Rust passthrough.
evidence: bootstrap.py:67; rust_core/ (zero --ltl); main.rs:310-318 for siblings
wave2: VERIFIED
false_positive_check: Native tg search --ltl must error (not search); Python path must accept.
owasp_or_rule_of_two: n/a (parity)
already_tracked: none (NEW)
recommendation: Add --ltl to SEARCH_PYTHON_PASSTHROUGH_FLAGS or native clap; parity test.
```

---

## Five mandatory lenses (session check)

| Lens | Result |
|------|--------|
| Unguarded prompt inject | N/A primary — tg is tool-layer; capsule renders bounded context |
| Degrade-path rot | Heuristic classify/GPU fallbacks set visible `fallback_reason` |
| Inert gates | #859 missing ratchet; #861 inventory/mermaid bullets inert in BACKLOG |
| Detect-only | MCP `_confine_*` returns consumed (AST census clean) |
| Dual-path drift | **DD-008 `--ltl`**; `--stats` Win/Linux |

---

## Ranked action queue (findings-only → fix later)

1. **#860 + DD-003 + DD-002 comment + DD-007 + #861 close** — docs honesty batch (`docs:`)
2. **#858+#859** — codemap `atomic_write_bytes` + Form-1 ratchet (write hygiene; sev LOW)
3. **DD-008/#865** — native `--ltl` passthrough
4. **DD-005** — `--stats` platform unify
5. **#862 / #863 / #864** — LOW CWE-88 / auth policy batch
6. **DD-004 / DD-006** — INFO / banked
