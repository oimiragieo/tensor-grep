---
name: tensor-grep
description: Use tensor-grep for repository code search, symbol lookup, blast-radius analysis, and edit planning when solving codebase tasks or preparing patches.
---

# Tensor-Grep Skill

Use this skill when you need to locate code precisely, understand likely edit impact, or prepare a minimal patch in a real repository.

## When To Use

- You need the primary definition or source block for a symbol.
- You need references or likely callers before editing code.
- You need an edit plan, blast radius, or validation target instead of ad hoc grep loops.
- You are preparing a patch and want a smaller, more accurate context bundle.
- You need a fast codebase orientation capsule (central files, entry points, symbol map) before diving into symbol lookup.
- You need to find code by text/content relevance rather than an exact symbol name.
- You need to resume or persist cross-session repo-map context — use `tg session` to cache the repo-map, then call session-scoped commands (`tg session context-render`, `tg session edit-plan`, `tg session blast-radius-render`) without re-indexing on each invocation.

## Argument Order

All symbol commands are **path-first**: `tg <command> <REPO_PATH> <SYMBOL>`.
A reversed `<SYMBOL> <REPO_PATH>` call is auto-corrected with a stderr hint, and
a single `tg <command> <SYMBOL>` resolves against the current directory — but
prefer the canonical path-first form.

## Default Workflow

1. Confirm the installed CLI is available:
   - `tg --version`
0. (Unfamiliar repo) Orient — single repo preferred; workspace root works (~4.9s cold-scan, last measured v1.95.0; a warm session-daemon hit is faster still):
   - `tg orient REPO_PATH`
   - `tg inventory REPO_PATH --json`
2. File deps (cheap):
   - `tg imports FILE` / `tg importers FILE [ROOT]` — absolute paths; importers may be deadline-partial
3. Content search then source:
   - `tg search PATTERN REPO_PATH --rank`
   - Vocabulary mismatch: `tg find "intent" REPO_PATH/src --deadline 20 --json` (run `tg install-dense` once; see `tensor-grep-find-and-route`)
   - Multi-project: `tg search PATTERN . --glob "*.py" --max-depth 3` (bare search on a defaulted PATH refuses in ~1.7s — a generic >1500-file `IMPLICIT_SEARCH_WALK_FILE_CEILING` probe, not just a vendored-root check; bypass with an explicit PATH, `--max-depth`, or `--allow-broad-generated-scan`)
   - `tg source REPO_PATH/src SYMBOL`
4. Symbol navigation — prefer `src/`:
   - `tg callers REPO_PATH/src SYMBOL --deadline 15 --json`
5. Edit readiness — **prefer `tg prepare REPO/src`** (~27s PASS, last measured v1.91.0):
   - `tg prepare REPO_PATH/src "task" --json`  # primary + blast floor + validation + coordination hooks
   - `tg prepare REPO_PATH/src "task" --out capsule.json --json`  # also persists the full capsule to FILE (byte-identical to stdout JSON; symlink/dangling-symlink/dir refused; feeds `tg evidence emit --capsule FILE` directly, no manual redirect)
   - `tg prepare REPO_PATH/src "task" --claim --json`  # also submit advisory ledger claim; anonymous claims stamp `coordination.claim.agent_id_hint` unless `TG_LEDGER_AGENT_ID` is set
   - Fallback loop: `tg agent` + `tg route-test` (budget 90s) if prepare unavailable
   - Whole-repo: **explicit** `--deadline N` on prepare/agent; bare `tg agent REPO` still TIMEOUT empty @75s
   - Mega-repos: narrow PATH; deadline partials often null symbol
5a. Multi-agent ledger — see `tensor-grep-ledger`. Claim/release/list now canonicalize to the nearest
   `.git` ancestor (worktree-aware, one store per repo) — `list` rolls scope UP, so the PATH-mismatch
   footgun from earlier dogfood rounds is fixed:
   - `tg ledger claim REPO --symbol SYM --agent-id AGENT --json`
   - `tg ledger list REPO --json`  # or any subtree PATH under REPO — rolls up to the same store
   - `tg ledger record REPO --receipt receipt.json --symbol SYM --agent-id AGENT --json`
   - `tg ledger find REPO --symbol SYM --fresh-only --json` then `release` (a zero-match release with
     `--claim-id`/`--symbol` emits `unmatched_reason` + `live_claims_elsewhere`; a bare-path release
     with neither fails closed)
5b. Evidence receipt:
   - `tg evidence emit REPO_PATH --capsule capsule.json --query "task" --json --agent-id AGENT`
   - Note `TG_CAPSULE_INLINE_CALLERS` (default-OFF): when set, `tg agent`/`tg prepare` prepend
     `# tg: callers=N (top: a, b)` to the primary snippet and add `snippets[i].inline_structural_annotation`
     (~+2.8% tokens) — reuses already-collected blast-radius evidence, no new scan.
5c. Skip `tg codemap` on WSL (TIMEOUT 90s)
5d. GPU: see `tensor-grep-gpu` — default loops stay CPU
6. Make the smallest correct edit from primary targets.
7. Run only the returned validation commands.
8. Cached loops: `tg session open --json REPO_PATH` then `tg session context-render SESSION_ID ABS_ROOT "query"`
9. Enterprise: `tg review-bundle create --manifest … --json`

## Registration-Audit Workflow (blast-radius before claiming done)

When you add an entity that must be registered in multiple places (a command, a flag, a route, a hook), enumerate ALL its registration sites BEFORE claiming the change is done — missing one fails *quietly*. The default audit path:

1. **Blast radius** — `tg callers PATH SYMBOL --json` lists every call site (file:line). On a real billing repo it surfaced 2 webhook handlers + 1 reconcile cron in ~1s — a 10-minute grep-and-read became a one-second decision.
   When the JSON has `"result_incomplete": true`, the call-site list was TRUNCATED by a scan/output cap — treat coverage as partial; do not conclude unlisted sites are safe. Human mode emits a loud stderr caveat.
2. **Pattern bugs** — `tg scan PATH --ruleset RULESET` runs a built-in security/compliance rule pack across those sites (see `tg rulesets` for pack names). `--config sgconfig.yml` and `--rule FILE` are separate options for a custom ast-grep config or a single rule file — not for built-in packs.
3. **Diagnostics** — `tg doctor --with-lsp`.

For registration-completeness specifically: `tg callers PATH REGISTRATION_FUNCTION` lists *callable* registrations — but the call graph can't see set/list/decorator registrations (allow-lists, `@router.post`, dispatch tables), which are often the missed site, so grep / `tg scan` those too. Your new entry must appear in ALL sites. (General principle: `verify-plan-against-code` Hard Rule 6; call-graph blind spots: `tensor-grep-code-audit` P7.)
A resolved zero-caller result is NOT dead code either — the call graph can't see set/list/decorator/dispatch-table registrations; cross-check with `tg scan` or grep before removing a zero-caller symbol. As of v1.17.1 the registration-completeness checker (`extract_members`) is string/comment-aware, so `#`-commented entries are no longer surfaced as false members.

`tg imports`/`tg importers`/`tg blast-radius` now report a relative dynamic import (`import_module(".x", package=...)`, `__import__(..., level>=1)`) as `dynamic_unresolved` — the literal text is preserved in `unresolved`, and it is NEVER silently resolved to a same-named decoy top-level file (both forward and reverse directions, and excluded from blast-radius's reverse scoring prefilter too). A wrong edge is worse than a missing one; treat `dynamic_unresolved` as "re-check yourself," not as a resolved dependency.

## Non-Interactive Mode

- When running in `claude -p` or other non-interactive automation, do not ask for confirmation.
- Use `tg` against the repository path that was added via `--add-dir`.
- After `tg` identifies the likely file/span, make the change directly instead of stopping at analysis.
- If direct editing is unavailable, emit a clean `git`-style unified diff only.

## Rules

- Prefer `tg` over repeated manual grep loops when working inside a real repository.
- Run `tg orient REPO_PATH` first when entering an unfamiliar repo — it gives centrality, entry points, and a symbol map in one call, and costs no API key or GPU.
- Use `tg search PATTERN PATH --rank` for content/text search; prefer it over raw grep loops when relevance ranking matters. The `--bm25` flag is an alias for `--rank`.
- Keep edits narrow and grounded in the files `tg` ranks highest.
- Do not expand context blindly if `tg` already identified the primary file and span.
- Use provider-backed modes only when a task is clearly about semantic ambiguity.
- In non-interactive mode, do not return “want me to apply this?” style responses.

## Known Issues

**Last full workspace+GPU dogfood: v1.91.0** (WSL `/mnt/c/dev/projects`, `/tmp/tg-dogfood-v21/report.tsv` — 57 PASS / 8 INCOMPLETE / 2 TIMEOUT / 1 FAIL). 14 items have shipped since (v1.91.1→v1.93.2: cold-path SLA, CLI-dispatcher ranking fix, single-file rayon, accuracy-gate pinning, inline caller annotations, binary-detection parity, flat-scorer hardening, index-lock de-flake, unscoped fast-refuse, dynamic-import honesty, WSL GPU-probe fix, install-dense/doctor-autostart/prepare-out UX batch, ledger PATH fix, gate-findings close-out, blast-radius honesty) — not re-verified as one whole-workspace sweep past v1.91.0; the rows below reflect the shipped fixes individually, not a fresh 1.93.2 dogfood run.

**More shipped v1.93.3→v1.95.0** (also not re-verified as one fresh whole-workspace sweep): a CEO +10% perf campaign (+25.3% end-to-end, v1.93.3-v1.93.8), a post-campaign repo-map ast.walk-merge (~54% faster) and validation-scan textual pre-check (~68% faster), a macOS CI rustup-retry fix, and two new symbol-graph languages — Java (v1.94.0) and PHP (v1.95.0) — taking the tier to 7 of the top-10 languages as of v1.95.0 (python/js/ts/go/rust plus java/php; C# merged to `main` right after v1.95.0 with its release still pending — re-check `lang_registry.py`'s `register_language` calls for the live count rather than trusting this number; C/C++ still deferred). A narrower same-session spot-check on that same workspace (c:/dev/projects, now 300k+ files, v1.95.0) found `orient` ~4.9s cold-scan (bounded by the 2000-file scan ceiling + centrality; a warm session-daemon hit is faster still — was ~36s at v1.91.0 on this workspace), `search` degrading to a partial result with an honest "exceeded timeout" message instead of hanging (exit 124), and `inventory --deadline` bounding per-project — encouraging, but still narrower than a full PASS/INCOMPLETE/TIMEOUT/FAIL sweep.

**Detail on a few v1.91.1→v1.93.2 items, still current at v1.95.0:** `tg prepare --out FILE` (persists the capsule for `tg evidence emit --capsule FILE`, no manual redirect), ledger claim/list PATH-canonicalization fix (A13), the generic >1500-file unscoped fast-refuse (A9), `dynamic_unresolved` import honesty (A10/A15), `tg doctor` `session_daemon.autostart` (A12(b)), and every dense-absent hint now leading with `tg install-dense` (A12(a)). See `tensor-grep-prepare`, `tensor-grep-ledger`.

**Prefer `tg prepare REPO/src`** over the multi-step agent loop for routine edits. Whole-repo prepare/agent with `--deadline` still partial/null-symbol; bare agent TIMEOUT empty @75s.

**`tg install-dense`:** once per host; post-install `tg find` drops the BM25-only `rank_fallback_reason` (the fallback message itself now leads with `tg install-dense` when dense is absent).

**`tg ledger`:** claim/release/list/record/find — see `tensor-grep-ledger`. Slice 1 (claim/release/list) is PATH-canonicalization-fixed (A13); Slice 2 (record/find) is still literal-path-rooted.

**Unscoped multi-project search refuses fast (~1.7s, not a silent 60s timeout).** A single `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` fast-refuse fires on any defaulted PATH >1500 files, coherent across all 3 doors (bootstrap probe, `main.py`, `rg_passthrough.rs`); escape hatches are an explicit PATH, `--max-depth`, or `--allow-broad-generated-scan` — `--glob`/`--type` alone do NOT bypass it when the path was defaulted. Prefer per-repo for deep `--type ts`.

**`tg codemap` still TIMEOUT on WSL** (90s). Importers/callers-at-root may be deadline-partial.

**CLI traps:** `tg classify` has no `--json`; scan ruleset names from `tg rulesets`; session `context-render` needs absolute session-root PATH.

## GPU (experimental) — verified on v1.91.0, no change through v1.93.2

Hardware visible (2× ~12GB). Build without CUDA: calibrate FAIL; search GPU → CPU fallback; doctor `search_ready=false`. v1.93.0 (A11) fixed the WSL bare-shim cross-domain misclassification that produced a bogus `path_not_found`; full detail in `tensor-grep-gpu`.


## Provider Modes

- Default: `native`
- Optional:
  - `tg defs REPO_PATH SYMBOL --provider lsp`
  - `tg blast-radius REPO_PATH SYMBOL --provider hybrid`

Use `lsp` or `hybrid` only if native lookup seems ambiguous or incomplete.

## Patch Guidance

- Prefer editing files directly if your tools allow it.
- If you must emit a patch, make it a `git`-style unified diff with `diff --git` headers and enough context lines to apply cleanly.
- Avoid unrelated files, caches, summaries, or prose.

## Reference

See [REFERENCE.md](REFERENCE.md) for current command patterns and examples.
