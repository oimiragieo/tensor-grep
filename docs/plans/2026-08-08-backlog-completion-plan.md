# Backlog / Known-Bug Completion Plan — refreshed 2026-08-08 (Round 2, thinktank-amended)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development +
> superpowers:executing-plans + tensor-grep-change-control. Every item is a PR-sized TDD slice.
> Work ONE ranked item per iteration (the orchestrator loop); never `git add .` (shared tree has
> in-flight edits — see "Shared-tree constraint" below).

**Goal:** land 100% of the remaining buildable audit fixes and drain every open PR to ZERO backlog;
push every research item through its gate (design council → TDD build → pin-first ranking gate) with
an honest disposition; leave NOTHING that is buildable unbuilt and nothing that is gated undisclosed.

**Base:** `origin/main` = `e60e2d8` (v1.110.5). Open PRs at base time: #966 (Task2A RED, do-not-merge),
#967 (docs — **head `738faed6` has 2 REAL failures**: `repo-hygiene` + `Analyze (rust)`; rebase onto
post-drain `origin/main` → re-push → verify the failures are base-staleness, fix if real), #975 (M7,
fully GREEN, mergeable), #976 (M8, fully GREEN, mergeable), #977 (ci/money-saver, draft mid-CI).

**Audit status:** Round 2 amended per three-lens thinktank (evidence / process-class / security).
Security lens returned FIX-FIRST; the plan below folds in ALL of its HIGH findings (M1 leaf identity
+ junction fixtures, M3 five-field vacuous-`all` hole, M17 unreachable-RED + canonical-root design,
H2 honor-vs-schema-divergence + scope) and the process lens's MUSTs (H2 cannot use the line-granular
`count` bool for occurrence counting; ratchet must be exhaustive-destructure with disposition
buckets + behavioral arms; the two exhaustive field-classification tests and both front doors and
the pinned count-matches tests must reconcile in the same PR).

**Shared-tree constraint (binding):** the main checkout is a STALE divergent branch
(`audit/h6-cudf-backend` @ `d9e477b`, base pre-#968 `bb4fdae`) carrying UNCOMMITTED in-flight edits
(`.claude/skills/*`, `AGENTS.md`, `docs/SESSION_HANDOFF.md`, `docs/audits/2026-08-06-enterprise-closeout-campaign-state.md`,
`backend_cpu.rs`, `ast_backend.py`, `ast_wrapper_backend.py`, `evidence_signing.py`, `repo_map.py`,
`test_ast_wrapper_backend.py`, `test_cli_modes.py`, `test_evidence_signing.py`,
`test_native_walk_error_ratchet.py`, `test_release_workflow_configuration.py`).
**Bind the rule to this FILE LIST, not to a label** (the set plausibly mixes M7/M8 + #977-validator +
H1-adjacent ratchet work). **NEVER stage or modify any of them.** All implementation here happens in
a fresh worktree created/rebased onto POST-drain `origin/main` (A22 union-rebase). Do **not** start
the worktree or any build until the Part-0 drain is underway or complete (A1 WIP cap: >5 undrained
PRs or red main = no new build dispatch).

---

## Part 0 — Drain the open queue (START HERE, in this order)

| PR | item | class | action |
|---|---|---|---|
| #975 | M7 verify_receipt never-raises | `fix:` (releases) | head already fully green → merge, wait for PyPI publish TAIL (A33 2nd window; tag-without-wheel is version-soup) |
| #976 | M8 AST invert fail-closed + cache key | `fix:` (releases) | WAIT for #975's PyPI publish to COMPLETE, then merge (one-per-publish serialized); then its own publish tail |
| #977 | spend-smart CI gate | `ci:` (no release) | let its own full-matrix run green; batch only in a completed-green gap, never inside a release window |
| #967 | CEO update docs + A77–A82 | `docs:` (no release) | rebase onto post-drain `origin/main` → re-push → re-verify the 2 failures (repo-hygiene, Analyze(rust)) are base-staleness; fix if real; then merge in a green gap |
| #966 | Task2A RED scaffold | `test:` | **DO NOT merge** — RED by design, now also base-CONFLICT; keep parked |

Merge discipline (A13/A31/A32/A33): releasing PRs serialize one-per-publish with the newest-main-run
COMPLETED gate (completed, not green); the publish tail (wheels + native assets + PyPI) must serve
before the next merge; `release-intent` skipped proves nothing (A33).

---

## Part 1 — Buildable audit fixes (P/M items, ranked)

All seams re-derived against `origin/main` `e60e2d8` by the thinktank audit (round 2). **Re-verify
the SYMBOL at build time** (line numbers drift). Every item: seam re-check → RED test (prove fail
pre-fix) → GREEN minimal → gate (ruff + `ruff format --check --preview .` + mypy + targeted suite) →
adversarial cold-read audit → fix → surgical commit → draft PR via the [coding loop](#coding-loop).

### P5 · H2 — native `--json`/`--ndjson` route must REFUSE `--count-matches` / `--files-with-matches` / `--files-without-match`, not silently drop them

**Verdict:** VERIFIED (PARTIAL per the audit census: `-o`/`only_matching` IS honored — keep it).

**Seam (origin/main, re-derived):**
- `rust_core/src/main.rs:8285-8325` — `search_requires_ripgrep_passthrough` gates `count_matches`/
  `files_with_matches`/`files_without_match` behind `!json && !ndjson`, so on the structured-output
  route those flags reach the native engine which DROPS them: `:3485`, `:3522`, `:3523`, `:3584`
  (`// OUT_OF_SCOPE_GAP`).
- Native config builders: `native_search_config_for_positional` `:8371-8405` (maps `count` :8389,
  `only_matching` :8397) and `native_search_config_for_command` `:8407+` (maps `count` :8445,
  `only_matching` :8453) — neither maps count-matches/files-*. **The `count` bool is LINE-granular
  `--count`, NOT occurrence counting — it MUST NOT be used to "honor" `--count-matches`.**
- Python: `cli/main.py:3860-3879` `_can_delegate_to_native_tg_search` returns False for
  `files_mode`/`files_with_matches`/`files_without_match`; `bootstrap.py:562` documents the
  `--count-matches` front-door exclusion (with the #121 occurrence-count rationale, pinned by
  `test_cli_bootstrap.py:2886-2934` and `tests/e2e/test_output_golden_contract.py:95-106`).
- The LIVE reachable path to the silent drop is the `TG_RUST_FIRST_SEARCH=1` OR-branch
  (`bootstrap.py:1546-1548`, `:1530-1539`) — **the RED test must drive THAT route**, not only direct
  native dispatch.

**Fix (fail-closed — HARD REFUSE, never honor-on-native, never silent):** **scope = EVERY native
engine route where the flag would otherwise be dropped** — the `--json`/`--ndjson` structured
routes AND the positional `--gpu-device-ids` path (the positional twin's gap fields are reachable
unconditionally via `run_positional_cli`'s `--gpu-device-ids` door, which has no json/ndjson gate).
A non-JSON request that is ROUTED TO RG PASSTHROUGH keeps its current honored behavior, as do
`--format rg --json` (rg passthrough), plain `--count`, and `-o`/`only_matching`. Rationale for
REFUSE-over-HONOR (correct derivations; do NOT re-cite rg-json refusals — rg does NOT refuse these
combos, it silently emits text-mode bare output): the #121 doctrine pins that NO rg-less engine
computes OCCURRENCE counts (`bootstrap.py:546-554`, `test_cli_bootstrap.py:2886-2934`,
`test_output_golden_contract.py:95-106`) — the native `count` bool is line-granular `--count` and
cannot honor `--count-matches` — and there is no rg-json emission schema for these three modes to
re-derive on the structured route. One behavioral RED per refused flag, on the real
TG_RUST_FIRST_SEARCH path. The positional twin gets its OWN REFUSED behavioral arm on the non-JSON
GPU path (`tg PATTERN --gpu-device-ids --count-matches` must exit-2 with the same distinct reason —
the ratchet label must match behavior at EACH door).

**Same-PR reconciles (class, one PR, not drift):**
- Both exhaustive field-classification tests that currently LICENSE these
  `OUT_OF_SCOPE_GAP`s: `main.rs:3477-3514` (`assert_search_args_gpu_field_classification_is_exhaustive`)
  and `:3578-3604` (positional twin) — the three fields move to a REFUSED disposition.
- Both front doors ship the SAME refusal (native `main.rs` + Python `bootstrap.py`/`main.py`), plus a
  `tests/e2e/test_routing_parity.py` lane asserting both doors refuse `--count-matches --json`
  IDENTICALLY (A27/A39 twin rule — the class fix must cross to the twin).
- The pinned count-matches semantics tests (`test_cli_bootstrap.py:2886-2934`,
  `test_output_golden_contract.py:95-106`) drive the NON-JSON route and stay GREEN AS-IS — the H2
  fix does not touch them; the fix ADDS new JSON-route refusal pins (one per refused flag), it does
  NOT re-pin the two unaffected guards.
- No new command/flag is added by H2, so the ADD registration sites
  (`SEARCH_PYTHON_PASSTHROUGH_FLAGS`, `_TG_ONLY_SEARCH_FLAGS`, `PUBLIC_TOP_LEVEL_COMMANDS`) are NOT
  required — stated explicitly so a later reader does not assume a registration re-baseline.
- **Deliverable = a compile-time-exhaustive RATCHET** (modeled on the GPU field-classification
  tests): every `SearchArgs` field on the NATIVE STRUCTURED route must be in exactly one disposition
  bucket — `HONORED` | `HARD-REFUSED` | `NO-OP-with-justification` | `MOOT` — via an exhaustive
  destructure (a new field fails COMPILE, cannot be satisfied by a comment; per the
  comment-satisfiable-census law). Behavioral arm per DISPUTED bucket: every field whose disposition
  CHANGES (the three newly-REFUSED fields) is asserted to actually exit-2 with its reason on the
  native JSON route AND on the positional `--gpu-device-ids` door; "assert every HONORED field
  actually emits" is a NAMED FOLLOW-UP row (bank it, do not over-build this slice). Scope the ratchet
  to the native structured route, not the whole binary (legitimately no-op fields like
  `unicode`/`no_config`/`messages`/`no_fixed_strings` keep a justified NO-OP bucket rather than
  forcing behavior change — the too-eager-gate law).
- Same-PR docs/validator pin (A19): the fail-closed paragraph in `docs/CONTRACTS.md`,
  `tensor-grep-config-and-flags`, `tensor-grep-run-and-operate` get the refuse-not-drop sentence.

### M1 — checkpoint create-side symlink/junction-ancestor containment (security)

**Verdict:** VERIFIED (narrow). **Seam (re-derived):** `_resolve_within_root` exists
(`checkpoint_store.py:149-165`) and **follows the LEAF** (`:161` `(root / candidate).resolve()`);
the create-side copy loop `:885-893` (`source = root / rel_path`, `follow_symlinks=False` only) has
NO ancestor containment; the undo pre-flight `:1311-1317` DOES
("(or, on Windows, junctioned) ANCESTOR directory"). `.git` scope deliberately keeps tracked
out-of-root-pointing symlinks in `entries` (`_git_snapshot_entries:623-627`, skips only
dir-gitlinks).

**Fix (A38-correct — do NOT copy `_resolve_within_root`'s leaf-following):**
1. Create-side containment resolves the **parent chain only** — `(root / rel_path).parent.resolve()`
   anchored to the pre-resolved root — then **lstat the leaf** and copy from
   `resolved_parent / leaf` with `follow_symlinks=False`. This preserves raw leaf identity (a
   legitimately tracked out-of-root-pointing symlink is COPIED AS A LINK, never refused and never
   followed) while refusing any ancestor that resolves outside the root. One-time root resolution
   does NOT freeze deep ancestors: resolve the parent fresh per file (narrow window), and record the
   A48 opened-parent-handle version (Event-gated parent swap on Unix AND Windows) as a NAMED
   follow-up row, not claimed.
2. The undo-side leaf-following residual (`:1312`/`:1315` via `_resolve_within_root:161` refusing
   tracked symlinks) gets its own named disposition (follow-up; do not silently inherit).
   **A49: both M1 deferred behaviors (A48 opened-parent-handle version; undo-side leaf-following
   residual) get a canonical tracker/owner row RECORDED IN THE SAME PR as M1**, not only in this plan.
3. **Windows is the attack platform — do NOT skip it.** Junctions (`mklink /J`, `New-Item -ItemType
   Junction`) need NO privilege. Games: (a) junction planted at a snapshot-tree ANCESTOR between
   anchor and copy (source read), (b) junction ancestor swap, (c) privileged symlink best-effort
   with `OSError`/`NotImplementedError` skip per the repo's standing Windows-symlink rule. Each
   fixture carries a Form-6 fixture-BITES precheck (assert the junction actually redirects before
   the probe runs).

### M3 — LSP `documentChanges` CreateFile / Rename / Delete confinement (security)

**Verdict:** VERIFIED — and the current guard is **VACUOUS for file-ops** (worse than the plan
assumed): `_workspace_edit_target_uris` (`lsp_server.py:116-130`) collects ONLY
`entry["textDocument"]["uri"]` (`:126-129`), so a CreateFile/RenameFile/DeleteFile-only
WorkspaceEdit yields `edit_uris == []` and the guard `if edit_uris and all(_uri_within_root(...))`
(`:759-762`) **PASSES VACUOUSLY** — out-of-root file-ops are forwarded to the IDE client today.
Enforcement helpers: `_resolve_repo_root` `:91`, `_path_within_root` (`:99-106`,
`root_resolved in target.parents`).

**Fix (fail-closed five-field + unknown-shape):**
1. Enumerate ALL FIVE target fields: `CreateFile.uri`, `RenameFile.oldUri`, `RenameFile.newUri`,
   `DeleteFile.uri` (plus `textDocument.uri`, already collected). Refuse the WHOLE edit if ANY
   target resolves outside the current workspace root. Specific opposites to pin with per-op REDs:
   rename-IN from outside (plants content in repo), rename-OUT (exfiltrates an in-repo file),
   delete of an out-of-root uri.
2. **Unknown member shapes fail closed:** any `documentChanges` member whose shape the parser does
   not recognize (cannot prove within-root) refuses the edit (A53: no weaker fallback). Replace the
   vacuous `all()` with this.
3. State the relay residual plainly in the test docstring + code comment: tg resolves at relay time,
   the IDE applies later — a filesystem swap between check and apply is an inherent relay-only
   TOCTOU (tg never opens the file); resolve() canonicalizes junctions/case/8.3 at CHECK time so the
   alias-escape class is covered there. Confinement is not an opened-identity guarantee; say so.
4. Per-op tests must exercise `_uri_to_path` edge shapes (percent-encoding, UNC netloc join at
   `lsp_server.py:79-80`) on both platforms.

### M16 — Rust `tg scan` drops composite rules + severity/message (correctness)

**Verdict:** VERIFIED (spot-corroborated: `backend_ast_workflow.rs:871-904` builds
`AstRuleSpec{id, pattern, language}` with no severity/message; `:1074-1099` single-pattern only).
**Fix:** TDD a composite rule (multi-pattern/conjunction) and a rule with custom severity + message
through Rust `tg scan`; fix the drop; gate on CI (Rust = CI oracle; author unit tests locally, no
cold whole-crate `cargo check` on the shared box — A12).

### M17 — reused index must not serve the wrong tree (correctness, fail-closed)

**Verdict:** VERIFIED. **Seam (re-derived):** `index.rs` stores `root` bytes (`:272-274`, read back
`:355-356`, `:413`); reuse branch `main.rs:9336` uses `loaded` without comparing stored root to the
query root; `staleness_reason` (`index.rs:782-856`) checks only no_ignore-mode + per-entry
mtime/size + new-file walk **over `self.root`** — never "is this still the queried tree".
CRITICAL constraint: `resolve_index_path` (`main.rs:9222-9228`) puts `.tg_index` INSIDE the tree, so
a plain "build root A, query root B" NEVER shares an index file — the naive RED is unreachable. The
real wrong-tree serve needs an ALIAS: same string, swapped tree (root symlink/junction flipped after
build, renamed tree, or a copied/tampered `.tg_index`).

**Fix:**
1. Canonicalize the stored root at BUILD (`lexical + canonical`, stored once).
2. At reuse/warm-load — **in BOTH load sites: the `preloaded_index` arm handed off by
   `detect_warm_index_state` AND the `main.rs:9336` fresh-reuse branch, and the check must run
   BEFORE `staleness_reason`/incremental update** — compare canonicalized QUERY root vs
   canonicalized STORED root; on mismatch, **REBUILD from the current tree** (always safe in-tree —
   prefer rebuild over bare refuse) with a disclosed reason (rebuild must not be invisible latency),
   never serve.
3. RED fixture = the ALIAS/SWAP shape (a symlink or junction under a fixed path that is swapped to a
   different dir between build and query; on Windows a junction), with the fixture-bites precheck.
4. Keep the per-file identity walk (`staleness_reason`) as the "still the queried tree" backstop.

### M14 — MCP `mcp_contract_version` stamping: make the existing choke point UNIVERSAL + a VALUE ratchet

**Verdict:** PARTIAL — **census corrected by the thinktank:** the central choke points ALREADY
EXIST: `_envelope_base` (`mcp_server.py:695-716`, stamp at `:709`) + `_inject_mcp_contract_fields`
(`:1125-1140`, applied at ~23 return sites). `tg_navigate` (`:6525`) is STAMPED on every path
(errors via `_meta_envelope`→`_envelope_base` `:767-770`; success delegates to stamped callees
`:3899/3910/3919/3932`, `:4269`) — the plan's earlier "≥2 unstamped incl. tg_navigate" was WRONG.
The verified unstamped tool is `tg_classify_logs` (`:5468`): raw inline `json.dumps` on error
returns `:5489-5495` and success `:5541-5556` — plus however many more a live census finds.

**Fix (do NOT re-implement the choke point):**
1. Route the raw-dict tools (start from `tg_classify_logs`, then a LIVE census via
   `server.list_tools` → response-shape scan — never a hand list) through the existing
   `_inject_mcp_contract_fields`/`_envelope_base` helpers.
2. **The ratchet asserts VALUE, not presence:** `mcp_contract_version ==
   _TG_MCP_SERVER_CONTRACT_VERSION` derived LIVE from the const — because `setdefault` at `:1138`
   lets a tool's own top-level literal WIN and skirt the central const; a presence-only ratchet
   passes exactly the spoofed/stale stamp it exists to catch. **Name the fix step explicitly:**
   change `:1138`'s `setdefault` to a HARD assignment for `mcp_contract_version` (the central const
   always wins), and state the ratchet reads the final injected wire.
3. Expect/account for the helper's re-serialization (indent/order changes) reddening exact-string
   contract tests → re-pin SUBSTANCE in the same PR; if concurrent mcp PRs land, apply the A22
   union-rebase.
4. `_TG_MCP_SERVER_CONTRACT_VERSION` stays `"1.7.0"` — this is stamping uniformity, no wire-shape
   change, so **do NOT bump** (the fifth-registration-site law: bump only when the shape changes).

---

## Part 2 — Research features (gated buildable; each needs its own design council before TDD)

Each R-item: design doc (with competitive/arXiv evidence) → thinktank council approves the DESIGN →
TDD build (default-OFF, additive, pin-first ranking gate where ranking is touched — A16) → dogfood.
Publishing any public number stays #72-gated; GPU stays #169.

| id | feature | gate | first slice |
|---|---|---|---|
| R2 | `tg index` wiring + daemon incremental | default-OFF; differential byte-identical proof | CLI-wire `semantic_index.py` (exists, unwired); add `tg index` command → 4-site registration → byte-identical find |
| R1 | typed graph edges (inherits/implements/instantiates) | default-OFF, additive | first slice = java + php (seam `lang_java.py:64-65`/`lang_php.py` `ref_kind="type"`; the `repo_map.py:5292-5293` cite is a JS/TS twin — verify the java/php seam at build) |
| R6 | git co-change in blast-radius | additive pin-first ranking gate (A16) | parse git co-change edges; pin CURRENT `blast-radius` output FIRST (A16) |
| R3 | query-policy layer (classifier + escalate + budget meter) | default-OFF opt-in; NO existing-semantics change; docs currently reject richer classifier (main.py:4386-area) — design + council FIRST | design doc accepting/rejecting the classifier; then default-OFF build |
| R4a | `tg edit-ready` (node/symbol-addressed apply) | experimental; **needs design** | seam note: `EditReadyTicketV1` is named in prose at `prepare_service.py:17` as explicit out-of-scope future work (the `:459-472` cite is `PrepareSnapshotV1`, a different dataclass) — design council → build |
| R4b | `tg slice <var>` data-flow | experimental default-OFF | design → TDD |
| R5 | line-level localization + tokens/correct private harness | private only; publish gated #72 | build harness + run privately |
| R7 | C/C++ cross-file + `#include` graph | **decision-gated; plan + council BEFORE build** | go/no-go council (compile_commands.json? vs current include-path engine) |

---

## Part 3 — Explicitly NOT auto-built (honest dispositions, not omissions)

- **CEO-gated (do not implement):** #48, #72, #77/F9, #131, #169 (financial stop). Closed-world
  maintenance only.
- **Blocked on shared-box rust/e2e ban:** F5 Steps 3-5 (rust_core/**), F6 remainder, F8
  (rust_core/src/main.rs, path_domain.rs, tests/e2e routing parity) → CI/cloud seats only.
- **Task 2A:** RED `6367614` is Sol `FIX-FIRST` (10 HIGH) — unpushed, no Actions; NOT merge-ready.
- **MCP-SURFACE (Task 4):** blocked on Task 2C (live contract version is 1.7.0; Task 4 plans 1.8→1.9).
- **Demand/research-gated:** #255, DD-006, AST-DSL-PARITY, MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH,
  RUST-REPLACE-SYMLINK — reopen on a concrete trigger.
- **Consistency note (A75 class):** the board's "READY: None at this snapshot" is about
  `README`/backlog rows, NOT a contradiction with Part 1's buildable audit queue — Part 1 items are
  audit-queue TDD slices (python-side locally gateable; rust-side CI-gateable), consistent with the
  board and its rust/e2e shared-box caveats. State this in the TASK_BOARD reconcile commit.

---

## Coding loop (per item — the orchestrator's micro-loop)

1. **Verify seam** against `origin/main` (grep the symbol, `git show origin/main:file`); never a stale
   branch or memory.
2. **RED test** proving the pre-fix behavior (Hang-class: anti-hang protocol; security: fixture-BITES
   precheck, Form 6; and for the H2 RED, drive the real `TG_RUST_FIRST_SEARCH` route).
3. **GREEN** minimal fix; then a passing-through regression pin (SUBSTANCE, not the bug's literal).
4. **Gate:** targeted suite → `uv run ruff check .` → `uv run ruff format --check --preview .` →
   `uv run mypy src/tensor_grep` → narrow pytest; Rust items → author unit tests + CI oracle.
5. **Adversarial cold-read audit** (fresh subagent, cite `file:line`, try to BREAK it) → fix →
   re-gate.
6. **Surgical commit** (only the item's files) with the conventional release-correct title
   (`fix:` for H/M, `feat:` for R that publish; docs/test/chore for the rest).
7. **Draft PR** on the branch; then drain one-per-publish; never auto-merge.

CPU-safe rule: no local whole-crate `cargo check`/benchmarks on the shared box (A12); route heavy
Rust/eval to CI or cloud subagents.

## Loop state / resume

- Base: `origin/main e60e2d8` (v1.110.5).
- Open: #966 (Task2A RED + base-conflict), #967 (docs, **2 REAL failures to fix first**), #975 (M7,
  green, ready to merge), #976 (M8, green, merge after #975 publish tail), #977 (ci, draft).
- Local main-checkout branch `audit/h6-cudf-backend` is STALE + dirty — do not build from it.
- Next actionable: **Part 0 drain (#975 → #976 → #977/#967)**, then **P5 · H2** (Part 1 item 1).