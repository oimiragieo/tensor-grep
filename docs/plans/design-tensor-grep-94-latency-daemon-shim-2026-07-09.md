# Design — #94: per-call latency (the #1 preference-killer)

**Verdict: GO, staged.** Sonnet verify-plan-against-code design 2026-07-09 (agent aa95d03d), read-only, ALL cost claims empirically measured on this box (not read from source). Cited `file:line` vs `main` @ 67f9779. Full agent output in session transcript 09e7d2aa. **Ship Part B FIRST (contained); Part A is load-bearing → council/Opus gate.** Builds collide with #480 (main.py) → gated on #480 harvest.

## Cold-start cost breakdown (measured, `-X importtime`)
`tg orient <emptydir>` = ~90% process-start + import, ~0% work. `orient`/`agent` always bounce native→Python sidecar (main.rs:3943, no native impl). The killer eager (module-level) imports in `main.py` (the 12,897-line Typer app, loaded for EVERY non-search command):
- **`main.py:31` LSP-provider-setup import ≈ 54ms** — the single biggest tensor_grep line item; transitively pulls urllib/ssl/http.client/zipfile/tarfile/email for an **opt-in/default-OFF** LSP auto-install feature irrelevant to orient/agent unless `--provider lsp/hybrid`. **Prime lazy-import candidate.**
- `main.py:27` ast_backend import ≈ 13ms (orient never does AST).
(Note: idle-box orient = ~380ms direct / ~575ms via launcher; the audit's 6.5s was under load — process/import cost balloons under contention, so the daemon win is UNDERstated by idle numbers. Ties to #83.)

## PART A — daemon as default fast path
### Seam
- Daemon: 127.0.0.1 loopback + 0600 secret + constant-time auth (session_daemon.py:51/70). `start_session_daemon` (:604) probes cheap (`_probe_daemon` :471, 0.5s ping) then spawns via `subprocess.Popen` (:663-678) **followed by a BLOCKING 5s wait-loop (:680-696, `_DAEMON_START_TIMEOUT_SECONDS=5.0` :60)** — THE LANDMINE.
- Two client entrypoints: `request_running_session_daemon` (:775, REUSE-ONLY, returns None on miss) vs `request_session_daemon` (:760, START-OR-REUSE, pays the 5s wait).
- **Routing today:** context-render/edit-plan = reuse-only + silent degrade (main.py:7399-7447 — the safe pattern to generalize); `session <verb> --daemon` = opt-in only; **orient/agent/defs/impact/refs/callers/blast-radius = NO daemon awareness.**
- **Server gap:** `_serve_session_request_from_payload` (session_store.py:1128+) handles defs/impact/refs/callers/blast_radius* BUT **has no orient/agent branch** — Tier 2 needs net-new server logic.

### Design (2-tier)
- **Tier 1 (ship first):** defs/callers/refs/impact/blast-radius — daemon already serves these; only client routing changes. New lazy-autostart wrapper generalizing main.py:7399-7447: try reuse-only (cheap) → on miss, kick a **NON-BLOCKING Popen (the :663-678 step WITHOUT the :680-696 5s wait)** → fall through to today's cold path for THIS call. **Call #1 never slower than today; call #2+ warm.** DO NOT reuse `request_session_daemon`/`start_session_daemon` as-is (the obvious-fix-is-wrong: the 5s wait makes the first cold call SLOWER).
- **Tier 2 (after Tier 1 mileage):** orient/agent — Tier 1 routing + NEW server handlers in session_store.py:1128+, output byte-identical to cold build_orient_capsule/build_agent_capsule + honor the exit-2-on-truncation 3-state contract (CONTRACTS.md already requires covering "warm-daemon fast-paths").
- **Opt-out:** `TG_SESSION_DAEMON_AUTOSTART` (default on; =0 restores cold/one-shot for CI/socket-restricted sandboxes). Follows the existing TG_SESSION_DAEMON_* naming.
- **Degrade FAIL-OPEN** (mirror-opposite of the Backend Fail-Closed Contract — call it out in review): copy the `except Exception: return None → cold path` at main.py:7399-7447. Do NOT copy the opt-in `--daemon` path (main.py:9649-9674) which fails-to-ERROR (no try/except). Only a SPEED path is skipped; no result is ever substituted.

## PART B — the ~200ms launcher-shim tax (CONTAINED, ship first)
### Seam (2 shims, both from scripts/install.ps1, governance-pinned)
- **Shim #1** "compat shim dir" `~/bin/tg` + `~/.local/bin/tg` (install.ps1:805-813): a `grep -qi microsoft /proc/version` WSL probe (fork) → exec shim #2.
- **Shim #2** "managed front door" `~/.tensor-grep/bin/tg` (install.ps1:733-752): another `grep` WSL probe (:735) → sets env → `[ -f "$TG_NATIVE" ] && exec tg.exe` else python -m.
- **Root cause of 2 hops:** the front-door dir is ahead of shim dirs on Windows User PATH (works for cmd/PowerShell/CreateProcess = 0 hops, `fresh_shell_path_tg_first_launcher_kind=managed-native`), BUT **git-bash/MSYS ignores PATHEXT** — matches bare `tg` → lands on shim #1 → chains to shim #2. `tg doctor` is BLIND to this (`_doctor_path_tg_candidates` main.py:2355-2393 probes PATHEXT order, bare `tg` last, so `tg.exe` always wins the candidate slot → false "native-exe" clean bill while bash pays the full tax).
- Pinned by test_install_scripts.py:278-291 + :294-303 (exact strings — WILL go red, update consciously).

### Design (2 additive levers)
1. **Collapse 2 hops → 1:** make the shim-dir files carry the front-door logic directly (unify $bashShimContent into $frontdoorBashContent shape). ~90-110ms saved.
2. **grep → builtin `[ -f ]`:** replace `grep -qi microsoft /proc/version` with a builtin existence test (try MSYS path first, WSL fallback) — mirrors the front-door's own `[ -f "$TG_NATIVE" ]` at :748. ~64-66ms/hop saved.
- **Combined: ~258ms → ~85-90ms** (inside the rg ~102-107ms baseline → flips "tg slower than rg per bash call" to faster).
- **Contract to preserve** (CONTRACTS.md + install.ps1): prefer release-native binary; set TG_SIDECAR_PYTHON/TG_NATIVE_TG_BINARY; python -m fallback; front-door-dir-ahead-of-shims on PATH; stage-then-atomic-swap (Commit-StagedManagedInstall :427-445); **the foreign-tg.exe guard (install.ps1:819-840, Test-TensorGrepLauncher :260-301) — the actual reason the launcher layer exists** — a collapsed shim MUST carry the same verification. Also fix `_doctor_path_tg_candidates` to probe bare `tg` per-dir so doctor stops false-negativing. **Check scripts/install.sh (POSIX) mirrors the 2-hop shape + its test_install_sh_* coverage — land symmetrically.**

## TDD + sequencing
1. **Part B lever 2 (grep→builtin) [CONTAINED, ship FIRST]** — install-script only, zero runtime/Python change, zero new processes. RED: extend the WSL-shim governance tests to the builtin content + a NEW bash-side wall-clock oracle test (NOT `tg doctor` — proven blind). 2. **Part B lever 1 (collapse hops) [CONTAINED, second]** + the doctor bare-`tg` probe fix. 3. **Part A Tier 1 [LOAD-BEARING, council/Opus gate]** — flips 5 flagship commands' default + spawns background processes by default for the first time. THE critical test: call #1 not slower than baseline (guards the 5s-wait landmine) + call #2 faster + `AUTOSTART=0` byte-identical + fault-injection (unwritable/permission-denied socket) still succeeds via cold fallback. 4. **Part A Tier 2 [LOAD-BEARING, after #3 mileage]** — warm-vs-cold output-identity + exit-2 contract.

## Risks
Part A: (a) the 5s-wait landmine reintroduced by careless reuse of request_session_daemon; (b) lingering daemons if idle-shutdown misfires; (c) Tier 2 server output diverging from cold (CONTRACTS.md scar); (d) opt-out must work in genuinely socket-restricted sandboxes (real permission-denied test, not a mock). Part B: (a) breaking the foreign-tg.exe guard; (b) governance tests "fixed" by rewriting assertions WITHOUT re-verifying via the live bash timing harness (self-fulfilling-test trap); (c) **real WSL not tested in this pass** (none available) — the `[ -f ]`-first logic is unverified end-to-end on WSL; close before shipping. **#83 link:** the flake is OS process-creation variance around the native tg invocation (DEFAULT_HELP_PROBE_TIMEOUT_MS=750 python_sidecar.rs:21 has huge headroom under 6.0s); Part A/B lower system-wide process-creation overhead → plausibly stabilizes #83 as a side effect.
