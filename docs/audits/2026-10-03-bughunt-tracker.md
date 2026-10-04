# Bug hunt 2026-10-03 — tracker (main @ e60f6ed / tg 1.123.11)

Ten read-only Sonnet auditors, one per area, navigated with `tg` (chunks A-J below). Each finding
carries the auditor's status (`V` = reproduced or unambiguous code path, `H` = hypothesis). `OV` =
additionally re-reproduced by the orchestrator. Raw per-chunk reports (with probe commands/output) were
kept in session scratch; this file is the durable record.

**Disposition legend:** `W1`/`W2`/`W3` = fix wave · `DUP->X` = merged into X · `DEFER` = valid, below the
line (reason given) · `REFUTED` · `BY-DESIGN` · `VERIFY` = hypothesis, must reproduce before planning.

**Process per wave:** plan (writing-plans) -> thinktank artifact gate (hash-frozen, until APPROVE) ->
Sonnet TDD build in worktrees -> codex (Sol) adversarial audit -> ruff check + `ruff format --preview` ->
PR -> CI green -> merge (burst-then-hold) -> verify on merged main -> update this table.

## Cross-cutting root causes (fix the class, not the instance)

1. **Python re-derives what rg/Rust already computed, and silently disagrees** — I-01, I-02, I-03, I-04, A-02.
2. **Undecodable / oversized / unparseable files vanish with a COMPLETE answer** (Backend Fail-Closed
   violation) — C-01, C-02(=B-02), D-01, H-04, H-05, J-01, I-05, I-06, D-11.
3. **`str.splitlines()` vs parser line numbers** — C-07, H-06, I-06 (lang_java/lang_php already fixed it, F26/#63).
4. **ASCII-only identifier regexes** — C-08, D-07.
5. **Error -> exit 1 (= "no match")** — J-02, J-05, J-09, A-06, H-09, B-04/G-02, B-07, B-09.
6. **Stale-lock reclaim race (check-then-unlink)** — F-05, F-10, J-13.

## Findings

| ID | Sev | St | Title | Disposition |
|---|---|---|---|---|
| A-01 | P1 | V/OV | Python-door `--` sentinel inserted before bundled short flags (`--json -C2 FOO a.txt`). Native door OK (OV) | W2 |
| A-02 | P1 | V | Python `re` pre-validation rejects valid rg regexes (`\p{Lu}`, `\x{61}`, `(?-i)`) | W2 |
| A-03 | P2 | V | `[[:digit:]]` FutureWarning on stderr | W2 (with A-02) |
| A-04 | P2 | V | did-you-mean truncates alphabetically before ranking (both doors) | W2 |
| A-05 | P2 | V | `-f/--file/-e` miscounted as explicit-path detection | W2 |
| A-06 | P2 | V | bad `TG_NATIVE_TG_BINARY` -> traceback, exit 1 | W2 |
| A-07 | P2 | V(code) | `_windows_user_path_value` "" on any OSError -> could overwrite User PATH | W2 |
| A-08 | P2 | V(code) | COM bridge refresh non-atomic copy | W3 |
| A-09 | P3 | V(code) | detached refresh helper lacks installer hardening (substring version check) | W3 |
| A-10 | P3 | V | defaulted-scope note printed under `-q` | W2 |
| A-11 | P3 | H | `--version --verbose` hardcoded feature claims (pinned by test) | VERIFY / likely BY-DESIGN |
| A-12 | P3 | V | arg scans continue past `--` | W2 |
| B-01 | P1 | V/OV | `tg source` text mode never prints the source | W2 |
| B-02 | P1 | V | >2MB files skipped -> not_found, result_incomplete false | DUP->C-02 |
| B-03 | P1 | V | nonexistent PATH silently re-anchored to cwd/ancestor (session open, checkpoint create, ledger, warm daemon) | W2 |
| B-04 | P1 | V | diff-impact: git failure -> risk=low; `--fail-on-risk bogus` | DUP->G-02 |
| B-05 | P2 | V | blast-radius-plan/render, edit-plan, context-render exit 0 on unknown symbol | W2 |
| B-06 | P2 | V | `tg repair-env --check/--dry-run` missing (dogfood report confirmed) | W3 |
| B-07 | P2 | V | session_* handlers: no JSON error envelope, exit 1 for crash and not-found | W3 |
| B-08 | P3 | V | `--json` envelopes inconsistently carry version/schema_version | W3 |
| B-09 | P2 | V | refs/callers: defined-but-unreferenced -> not_found true | W2 |
| B-10 | P3 | V | `--class` only on defs | W3 |
| B-11 | P3 | V | `_plain_json_incompatible_render_flags` dead in production | W3 |
| C-01 | P1 | V | Python file with UTF-8 BOM / syntax error / latin-1 vanishes from symbol graph | W2 |
| C-02 | P1 | V | >2MB files silently skipped by symbol graph | W2 |
| C-03 | P1 | V | token budget keeps 64KB `imports`, cuts callers 19->1 | W2 |
| C-04 | P1 | V | `_is_test_file` misses Go/Java/TS conventions; absolute-path `tests` ancestor misfire | W2 |
| C-05 | P2 | V | single test candidate emitted at 0.95 regardless of relevance | W3 (closed by C-13) |
| C-06 | P2 | V | pytest validation commands interpolate unquoted paths | W2 |
| C-07 | P2 | V | splitlines vs ast line -> wrong `text` | W2 (class 3) |
| C-08 | P2 | V | non-ASCII identifiers dropped; regex arm invents `caf` | W2 (class 4) |
| C-09 | P2 | V | regex fallback: Rust lifetime `'` treated as string; `/* call() */` counted | W3 |
| C-10 | P2 | V | Python callers name-only (params/locals/aliases) | W3 |
| C-11 | P2 | V | decorators excluded from Python def spans | W3 |
| C-12 | P2 | V | file-api: single-line brace fns flagged truncated | W2 |
| C-13 | P3 | spec | function-level code2test mapping (dogfood report 5.3) | W3 |
| D-01 | P1 | V | non-UTF-8 source silently dropped by every lang extractor | W2 (class 2) |
| D-02 | P1 | V | C# properties/fields/events, Java fields, PHP props, Go iface methods: no definitions | W3 |
| D-03 | P1 | V | C# `?.`/generic calls, PHP nullsafe calls not callers | W3 |
| D-04 | P2 | V | C++ explicit template-arg calls, Go `Sum[int]()` | W3 |
| D-05 | P2 | V | `.mts/.cts/.inl/.cu...` unmapped, silent | W2 |
| D-06 | P2 | V | `tg importers` empty COMPLETE answer for C/C++/Go/Java/PHP/C# | W2 (gap flag) / W3 (resolver) |
| D-07 | P2 | V | ASCII-only `_CLEAN_SYMBOL_NAME_RE` | DUP->C-08 |
| D-08..D-12 | P3 | V | C++ specializations, Go type alias, registry re-register, C# BOM regex, PHP group-use | W3 |
| E-01 | P1 | V | blast-radius `max_depth` unclamped (1e8 = 56s CPU via MCP) | W1 |
| E-02 | P1 | V | MCP `tg_ruleset_scan` write paths overwrite any file in root (source, `.git/config`) | W1 |
| E-03 | P1 | V | `tg_ast_search` malformed pattern -> "0 matches" | W1 |
| E-04 | P1 | V | `tg_search` no line-width cap (3MB response) | W1 |
| E-05 | P2 | V | session_show/repo_map/doctor unbounded MCP output | W3 |
| E-06 | P2 | V | `tg_search` bad args -> plain-text internal error | W2 |
| E-07 | P2 | V | followup_ref non-ASCII compare_digest TypeError (latent module) | W2 |
| E-08 | P2 | V | `budget_remediable` false for max_repo_files cap | W2 |
| E-09 | P2 | V | unsupported `lang` misreported | W2 |
| E-10 | P2 | V | `tg_find` accepts empty query | W2 |
| E-11 | P3 | V | MCP arg-semantics inconsistencies | W3 |
| E-12 | P3 | H | rewrite apply TimeoutExpired -> generic error, no partial warning | VERIFY |
| F-01 | P1 | V | repo-planted `daemon.json` redirects tg to attacker server (forged results / exfil) | W1 |
| F-02 | P1 | V | session on zero-context-file repo never goes stale | W1 |
| F-03 | P2 | V | daemon rebuild failure loses trigger, code `invalid_request` (dogfood report 4.1, refined) | W1 |
| F-04 | P2 | V | `checkpoint undo` deletes pre-existing untouched empty dirs | W2 |
| F-05 | P2 | V(code) | `index_lock` stale-reclaim race (two holders) | W2 (class 6) |
| F-06 | P2 | V(code) | `session_prepare` holds index lock across cold prepare | W3 |
| F-07 | P2 | V(code) | daemon stop can't stop version-skewed daemon; unlinks metadata | W2 |
| F-08 | P2 | V(code) | implicit session creation: global lock across open_session | W3 |
| F-09 | P3 | H | pre-auth per-recv timeout, unbounded threads | VERIFY |
| F-10 | P3 | V(code) | daemon start-lock reclaim race | DUP->F-05 |
| F-11 | P3 | V(code) | no single-flight on stale rebuild | W3 |
| F-12 | P3 | V(code) | index loaders crash permanently on extra field | W2 |
| G-01 | P1 | V/OV | `tg diff-impact <ref>` -> `git diff` flag injection (`--output=` writes a file) — CWE-88 | W1 |
| G-02 | P1 | V | diff-impact drops deletions/quoted/space paths; git failure = "no changes" | W1 |
| G-03 | P1->P2 | V | edit-ticket verify prunes `build/dist/target/venv/.git` at any depth -> undeclared edits PASS. **Severity lowered 2026-10-03 (orchestrator, positive-controlled search):** `edit_ticket_service` has NO CLI/MCP front door — only its own module and unit tests reference it (`tg edit-ready` is reserved, unimplemented) — so the hole is latent until edit-ready ships | W1 |
| G-11 | P3 | V/OV | `edit_ticket_service` (tamper-evident edit tickets) is unwired: no command or MCP tool reaches it; `tg edit-ready` is a reserved name that falls through to search (memory A90) | W3 (wire or delete — product decision) |
| G-04 | P1 | V | confidence ignores query-term coverage (0.94 on absent concepts) | W3 (with G-05) |
| G-05 | P3 | spec | abstention (`abstain`/`query_coverage`) + `tg trace` (dogfood report 5.1/5.2) | W3 |
| G-06 | P1 | V | token budget starves primary symbol (5 tokens) | W2 |
| G-07 | P2 | V | diff-impact exit 2 conflates breach vs incomplete; `>` vs `>=` | W1 |
| G-08 | P2 | H | unreadable file crashes ticket build/verify | W1 (with G-03) |
| G-09 | P3 | V(code) | one >10MB file -> every ticket population_incomplete, path unnamed | W3 |
| G-10 | P3 | V | `tg find` JSON has no score/rank | W3 (with G-05) |
| H-01 | P1 | V | re-running `tg install` corrupts Codex config.toml on Windows (`re.sub` backslashes) | W1 |
| H-02 | P1 | V | LSP didChange stores only the inserted fragment | W2 |
| H-03 | P1 | V | SARIF emits results for zero-match rules | W1 |
| H-04 | P1 | V | one non-UTF-8 file with regex hit aborts `tg scan` | W1 |
| H-05 | P1 | V | `tg scan` silently drops non-UTF-8 files from AST rules | W2 (class 2) |
| H-06 | P2 | V | file-api splitlines line shift | DUP->C-07 |
| H-07 | P2 | V(code) | `tg install` half-applies; `--yes` ignored | W3 |
| H-08 | P2 | V | `_update_json_mcp` crashes on non-object config; JSONC URL refusal | W1 (with H-01) |
| H-09 | P2 | V | scan `--baseline/--suppressions` missing file -> traceback exit 1 | W1 (with H-04) |
| H-10 | P2 | V(code) | doctor reports existing binary "missing" on probe timeout | W2 |
| H-11 | P3 | V | `tg sql` rejects `[a;b]` identifiers | W3 |
| H-12 | P3 | V | `enrich_search_items_with_containers` tested copy != shipped; misc | W3 |
| I-01 | P1 | V | `--json` column via Python `re` on user pattern: ReDoS (57s) + wrong column | W1 |
| I-02 | P1 | V | RipgrepBackend drops `-S`, `--stop-on-nonmatch`, `--engine`, ... | W1 |
| I-03 | P1 | V | RustCoreBackend binary pre-check runs user pattern via Python `re` over whole file | W1 |
| I-04 | P1 | V/OV | every `-c` routed to RustCoreBackend, ignores `-w/-x/-S` (`--stats -c -w` = 3 vs rg 2) | W1 |
| I-05 | P2 | V | FallbackReader re-yields whole file on late decode error | W2 |
| I-06 | P2 | V | AstBackend splitlines / raw UnicodeDecodeError / `.ts` grammar | W2 (class 2/3) |
| I-07 | P2 | V | StringZilla ignores smart-case and `-m 0` | W2 |
| I-08 | P2 | V | CPU backend drops trailing context after `-m`; no `is_context` | W3 |
| I-09 | P3 | V | DirectoryScanner `-t` literal ext, no followlinks, truncation ignored | W3 |
| I-10 | P3 | V(code) | device probes: narrow except, NVML codes, all-GPU fallback | W3 |
| I-11 | P3 | V | csv strip eats trailing spaces; NDJSON lacks column | W3 |
| I-12 | P3 | H | plaintext persistent index caches, no cap | VERIFY |
| I-pc | P3 | — | pre-commit wiring of the two deprecated-flag guards (dogfood report 4.2) | DEFER — 1.4s, already in CI, auditor judged not justified |
| J-01 | P1 | V | `--index` aborts on any non-UTF-8 file, exit 1 | W2 (Rust) |
| J-02 | P1 | V | crashed/killed rg child -> exit 1 (7 sites) | W2 (Rust) |
| J-03 | P1 | V | root door rejects `-e`, `-a`, `-L`, `--passthru`, ... that `tg search` accepts | W2 (Rust) |
| J-04 | P1 | V | `tg run --rewrite --apply` non-transactional, no partial report | W2 (Rust) |
| J-05 | P2 | V | `tg run` errors exit 1 | W2 (Rust) |
| J-06 | P1 | V | `-c --json` mixes plain counts and JSON on stdout | W2 (Rust) |
| J-07 | P2 | V | `--index` differs from rg on NUL/BOM/CR | W3 |
| J-08 | P2 | V | index same-size+mtime staleness | BY-DESIGN (documented M17-FU1) |
| J-09 | P2 | V | sidecar spawn failures exit 1 | W2 (Rust, with J-02) |
| J-10 | P2 | V | `editor_plane.rs` effectively unwired | W3 (delete-or-wire decision) |
| J-11 | P3 | V | `tg worker` single-threaded, no timeout, no token | W3 |
| J-12 | P3 | V(code) | rollback restore truncates in place | W3 |
| J-13 | P3 | H | Rust index_lock reclaim race | DUP->F-05 |

## Refuted / dogfood notes triaged

- `tg search -n` "prints no line numbers" (F notes): **REFUTED** (OV) — prints `509:`; unnumbered piped output = rg non-tty parity.
- Grammar-missing on this box's managed install (D, C notes): the JSON **does** disclose a
  `resolution_gaps` entry (OV), but `result_incomplete` stays `false` and text mode hides it ->
  folded into class 2 (W2). `scripts/install.ps1` requests `tensor-grep[ast,nlp]` (OV), so the cause is this box's install history, not the installer. Still open: why the venv lacks the extra.
- Other dogfood UX notes (sql error lists no columns, `tg agent` swapped args exit 0, `tg update --help`
  says `tg.exe upgrade`, mixed path separators in `--glob` output, comments citing a non-existent
  `tg index` command) -> W3 UX batch, each to be reproduced first.

## Residuals recorded by the plan councils (accepted, each with its reason)

| ID | Wave/Part | Residual | Reason / reopen trigger |
|---|---|---|---|
| R-01 | W2a F.1 | Bare 2-char `-z` in the pattern slot gets no `--` sentinel (`bootstrap_native_argv.py:97` gate is `len > 2`) | Pre-existing; `-z` only runs rg's fixed decompressors. Reopen if any 2-char flag gains exec semantics. |
| R-02 | W2a F.1 | All-dash-led remainder (`tg search -in`, no pattern) still searches for the literal `-in` | Gating it would turn `tg search -foo` into `rg -f oo`. Reopen on a user report. |
| R-03 | W2a G2.5 | Identifiers containing combining marks (decomposed accents, most Indic scripts) are rejected, never truncated | `\w` excludes Mn/Mc; full XID_Continue support is demand-gated. |
| R-04 | W2a G1.2 | Lossy-decode gap costs an extra read per non-cached file; files that legitimately contain U+FFFD disclose a gap | Fail-closed direction, `when_empty` only; timing measured in the PR body. |
| R-05 | W2a G2.3 | A git submodule / nested repo under `tests/` classifies relative to its own root | Rare; reopen on a ranking report. |
| R-06 | W2b H.5 | Mixed-version upgrade window and unlink-while-held can yield two lock holders | Old process must exit; acquire-time identity re-check narrows it. |
| R-07 | W2b H.6 | `_remove_daemon_metadata` re-read/unlink is not atomic against a successor publishing in between | Pre-existing (session_daemon.py:398-421, :2123). **Follow-up:** a publish-side lock around daemon.json writes (W3). |
| R-08 | W2b H.6 | Daemons built before wave 1 cannot prove identity, so `daemon stop` never stops them | They self-reap via the idle monitor (900 s default). |
| R-10 | W2a G1.3 | Multi-line TypeScript object/conditional return types (`(): { a: string;` + next line) may stop the signature at an internal `;` | Main's existing behaviour, disclosed as `signature_truncated`; three council rounds showed a general brace heuristic breaks rustfmt `where` clauses. **Owned: W3 item W3-SIG** (parser-backed signature span from tree-sitter `function_declaration` child ranges). Acceptance test: `build_file_api` on `function make(): { value: string;\n  n: number } {\n  return x;\n}` returns a signature containing `n: number` with no `signature_truncated`, AND the rustfmt `where ...,\n{` control stays complete. |
| R-09 | W1 C.4 | `tg_ast_search` plain-text cumulative byte cap is unreachable under its hard 150-line limit | Defence in depth; documented in the test docstring. |

## Wave status

| Wave | Scope | Plan | Thinktank | PRs | Merged |
|---|---|---|---|---|---|
| W1 | P1 security + silent-wrong (G-01/02/03/07/08, F-01/02/03, E-01..04, I-01..04, H-01/03/04/08/09) | `docs/plans/2026-10-03-bughunt-wave1.md` (final SHA-256 `C47E03AE`) | R1-R9 CHANGES_REQUIRED (verified + folded). R10-R14: 4/4 non-codex seats APPROVE every round; codex raised one new narrow item per round (R12-R14 test-level only), each verified and folded. **APPROVED BY ADJUDICATION (2026-10-03):** not two clean rounds on identical bytes — the convergence pattern of the 2026-10-01 denylist audit (new narrow item every round). Compensating control: every Part's diff gets the mandatory codex gpt-6.1-sol adversarial audit before merge. | building | — |
| W2a | parts F (front door), G1 (silent drops), G2 (budget/tests/identifiers); F.4 ships in the W2b I.7 PR | `docs/superpowers/plans/2026-10-03-bughunt-wave2a.md` | R1-R5 CHANGES_REQUIRED, folded; R6 `73F9B45C` running | — | — |
| W2b | parts H (CLI contracts/state), I (Rust), K1/K2 (LSP/MCP) | `docs/superpowers/plans/2026-10-03-bughunt-wave2b.md` | R1-R3 CHANGES_REQUIRED, folded (H.5 redesigned onto OS advisory locks; H.6 now proves daemon identity before stop); R4 `AB7E38A3` running | — | — |
| W3 | enhancements (abstention, tg trace, code2test, repair-env --check) + P3 | — | — | — | — |
