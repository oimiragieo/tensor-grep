# World-Class Roadmap — edit-control plane, not faster grep (2026-08-09, council-amended)

> **For agentic workers:** this is a DESIGN + PRIORITIZATION doc — NOT yet a build plan. Every item
> below is scoped (new vs banked), given its operating contract, and assigned a buildability verdict
> under the shared-box Rust/e2e ban + CEO gates. Route each item through `tensor-grep-change-control`
> + a thinktank design council + TDD before build. The council (ground-truth + adversarial + gate
> seats, 2026-08-09) ENDORSED the thesis and AMENDED the spine; the amendments are folded in below.

**Thesis (council-endorsed):** tg's moat is the agentic EDIT-CONTROL PLANE — deciding what to touch,
what else breaks, what to run, and when to stop, with receipts — not faster grep. The expensive 80%
(evidence emit/verify, review-bundle, ledger, checkpoints) is ALREADY SHIPPED; the remaining rails
exceed what any agent execution sandbox can do, which is the real moat:
1. **semantic blast-radius enforcement** (what must change when this changes),
2. **primary-span claim-checking** (did the edit touch the span the ticket authorized),
3. **escrowed, head-bound evidence** (receipts a CI-held key signs, not the editing agent's).

An agent's sandbox already enforces "touched files ⊆ allowed" natively — that is **table stakes, not
a PASS condition** (council HIGH-1).

**Grounding:** 2026-08-09 published dogfood (v1.110.10, gotcontext-saddle + tensor-grep) is 22/22 PASS
on the current CUJ; the 5 "FAIL" rows were triaged as bad oracles. Product truth verified on
origin/main `e0015f7`: `workspace_root_refused` exits 2 (`main.py:7915-7919`); `--json` bare search
stamps `path_was_defaulted`; orient/inventory/map/find/defs/callers/prepare/route-test/agent/
evidence/ledger all PASS; language coverage 10/10 parser-backed, foundational tier EMPTY
(`repo_map.py:570` descriptor). External dogfood PASS counts live in the dogfood artifact, not the
repo (council ground-truth note).

---

## Prerequisite hotfix (NOT the feature spine — council HIGH-9)

### H1 — Unknown-command fail-closed + PATH honesty (trust hygiene, ship first, rank separately)
**Problem (VERIFIED live):** unknown subcommands can fall through to search. On the PYTHON front
door `bootstrap.py:374-383` `_normalize_search_invocation` returns every unknown-first-arg as search
args, so `tg edit-ready --help` prints `Usage: tg search` exit 0 — agents conclude nonexistent
Phase-2 features exist. The native door (`main.rs:1425-1433`) rewrites unknown-first-arg + recognized
search-flag → search, but `--help` falls through natively as clap exit 2. So the `--help` example
fires via the Python door; flag-bearing unknowns (`edit-ready --json`) fall through BOTH doors.
**Contract:**
```
tg edit-ready --help
# wanted (both doors): exit 2, error.code=unknown_command, nearest=["prepare","evidence","review-bundle"]
tg doctor --json
# → adds: path_tg_version, pypi_latest, shadow_launchers[], contract_commands[],
#   hard warning row if PATH tg != claimed distribution
```
**Seams:** both front doors (`KNOWN_COMMANDS` `commands.py:9` + `normalize_top_level_search_args`
`main.rs:1785` / `is_known_python_command` `:7826` + `bootstrap._normalize_search_invocation`) must
refuse unknown subcommands with a distinct exit/error instead of feeding search; the 4-site
registration parity (AGENTS.md "Adding a Command") governs the both-door test.

---

## Feature spine (council-amended order)

### S1 — Fail-closed edit tickets + verify-edit, escrowed (BANKED F6, re-scoped; THE moat)
**Status:** F5 (Task 8 edit-ready; Step 2 shipped `build_prepare_snapshot` `prepare_service.py:487` +
`PrepareSnapshotV1` `:460`; Steps 3-5 BLOCKED on rust_core/e2e). F6 (Tasks 6-7 verify-edit): Step 0
shipped (#939), rest multi-week (~10 schemas, WSL path-domain, evidence signing, 5MiB reader) — NOT
purely rust/e2e-blocked (ground-truth correction). `EditReadyTicketV1` is designed-only, not shipped.
**Council corrections (HIGH-2/3/4/5/6 + ground-truth):** the Python slice is honest as "no *core-rust*
logic," NEVER "no native touch": the real surface is the managed native `tg.exe`, so T2's slice must
fold in BOTH front doors + `Commands::VerifyEdit` passthrough + `PUBLIC_TOP_LEVEL_COMMANDS` parity
registration, or its first dogfood fails with exactly the H1 bug. Rand PASS/FAIL must split
violated-vs-unverifiable. Ticket must carry a tree fingerprint. Un-apply must exist via checkpoint.
**Contract:**
```
tg edit-ready REPO/src "raise workspace_root_refused on parent refuse" --json
# → ticket_id { base_sha, working_tree_fingerprint }, primary_span, allowed_files[] (TABLE STAKES),
#   validation_commands[], expires_at, ask_user_before_editing
tg verify-edit REPO --ticket ticket.json --diff HEAD --json
# verdict ∈ { PASS, FAIL(edit_contract_violated), UNVERIFIED(reason) }
# PASS only when, with ESROWED evidence: validation subprocess stdout-hash+exit code+duration
#   signed by a key pinned via TG_EVIDENCE_TRUSTED_KEYS that the EDITING PRINCIPAL DOES NOT HOLD
#   (CI-held); primary span changed as claimed; no silent extras outside blast_radius_floor;
#   tree fingerprint matches ticket (no drift from sibling agents/rebase).
# FAIL closed: incomplete_reason_class=edit_contract_violated, exit 2 + revert-eligible file set
#   (ticket carries checkpoint_id; rollback-on-violation is the un-apply primitive).
# UNVERIFIED (runner-less repo / deadline-truncated validation / escrow key absent):
#   never certifies, distinct code, agent should ask the user.
```
**De-block move:** Python verify-edit contract + TicketV1 schema (+fingerprint+escrow fields) +
fail-closed verifier FIRST, with BOTH front-door enrollment + parity test in the same slice; the
core-rust search/walk logic stays a later CI/cloud slice. **S5 is this item's justification — bundle
them (council HIGH-9).**

### S2 — Registration-aware impact-diff (NEW; the tier-3 differentiator)
**Problem:** changing `_emit_broad_scan_refusal` should flag "update the refusal test class, the
multi-project-search skill, CONTRACTS.md exit-2 taxonomy" — not "zero callers, safe to delete."
**Contract:**
```
tg impact-diff REPO --against main --json     # or --patch file.diff
# → changed_symbols[], registration_sites[] (allowlists, @router, Commands:, class registries),
#   tests_to_run[], docs_skills_to_update[], confidence, resolution_gaps[]
```
**Council correction (LOW-10):** `resolution_gaps` must inherit the registration census's blind
spots — string/comment-aware extraction matching what CI's registration gate uses, failing closed on
non-parseable shapes (A36/A37; the `--rank`-in-a-set incident). Reuse blast-radius + the
registration-completeness machinery + `tg callers`; diff→symbol extraction is the new part.

### S3 — next_action machine protocol + budget envelope (NEW; capsule extension, default-OFF additive)
**Problem:** orchestrators must not parse English `suggested_scope`. **Contract:**
```
{ "next_action": { "type":"edit_file", "path":"src/…/main.py", "span":{…},
                   "instruction":"…", "stop_if":["ask_user_before_editing.required","result_incomplete"] },
  "on_success": { "type":"run", "argv":["uv","run","pytest","tests/unit/test_….py","-q"],
                  "deadline_seconds":300, "max_output_bytes":1_000_000,
                  "allow_network":false, "fail_closed_on_timeout":true },
  "on_failure": { "type":"narrow_scope", "suggested_path":"src/tensor_grep/cli" } }
```
**Council correction (MEDIUM-7):** every run envelope carries `{deadline_seconds, max_output_bytes,
allow_network=false, fail_closed_on_timeout}` (A56 cap-at-every-door).

### S4 — Warm session resume of decisions (BANKED PARTIALLY — correct the premise)
**Ground-truth correction (HIGH):** `tg session open/list/show/refresh/context/…` + daemon +
checkpoints ARE shipped (`main.py:12897`+); **`tg session prepare`/`resume` DO NOT EXIST** — only
top-level `tg prepare` (`main.py:10860`) — so "resume-of-decisions" is not merely missing, the whole
`session resume` gate is unbuilt. **Contract (as the designed forward surface):**
```
tg session open REPO --json
tg session prepare S123 "…" --json          # cached map + prior rejects
tg session resume S123 --json               # restores last capsule, open claims, verify-edit tickets,
                                            # suggested_scope; UNBUILT — do not claim
```

### S5 — Head-bound, escrowed evidence chain at CI (BANKED PARTIALLY; rides S1)
**Status:** evidence emit/verify + review-bundle create/verify shipped (CLI; `--require-verify-edit`
absent — 0 hits). Nit: evidence is CLI-only, no `tg_evidence_*` MCP tool (only `tg_review_bundle_*`).
**Contract:**
```
tg prepare REPO/src "…" --out capsule.json --claim --json
tg verify-edit REPO --ticket ticket.json --json
tg evidence emit REPO --capsule capsule.json --ticket ticket.json --sign --json
tg review-bundle create --manifest rewrite-audit.json --receipt receipt.json --against origin/main --json
tg review-bundle verify bundle.json --min-receipts 1 --require-verify-edit --json
```
**Council correction (MEDIUM-8):** stamp base/head/merge SHAs as SEPARATE fields + add a post-merge
verification leg; state otherwise as head-scoped only (A29/A44/A51). A squash shifts the commit, so
receipts must say whether the MERGED artifact contains the fix.

### S6 — Semantic default + why_ranked (BANKED PARTIALLY; additive find UX)
**Status:** `tg find` + BM25/dense RRF + `TG_FIND_DENSE_WEIGHT` (`main.py:4358`) + `tg install-dense`
shipped; `install_state`/`why_ranked` absent (0 hits). **Contract:**
```
tg find "where do we refuse multi-project workspace roots" REPO --json
# → hits with lexical_score, dense_score, structural_bonus,
#   why_ranked: ["phrase in incomplete_reason_class","symbol _emit_broad_scan_refusal"],
#   install_state: "dense_ready" | "bm25_only (run tg install-dense)"
```
**Honesty bound (A73):** the bare wheel lacks dense extras; make *state* explicit so agents never
silently trust BM25-only as dense.

### S7 — Federated workspace object (BANKED F8; Python slice first, rust/e2e later)
**Status:** F8 BLOCKED (`rust_core/src/main.rs`, `path_domain.rs`, `tests/e2e/test_routing_parity.py`).
**Contract:**
```
tg workspace open C:\dev\projects --json
# → workspace_id, members[{name,root,lang_mix,default_src}], refuse_policy
tg workspace prepare ws_01 "fix parent refuse class in tensor-grep skills" --json
# → routes to tensor-grep/src automatically; never walks agent-studio unless asked
tg workspace callers ws_01 "workspace_root_refused" --across dependents --json
```
**Council correction (HIGH-3, applies):** as with S1, the Python workspace OBJECT + per-member routing
must include both front doors + parity registration, or the native `tg` surface refuses it. The
rust_core/main.rs + path_domain.rs + tests/e2e half stays CI/cloud.

---

## "Day in the life" (acceptance narrative)

Given "fix parent refuse class naming across product + skills":
1. `tg workspace prepare projects_ws "parent refuse class" --json` → routes to tensor-grep, lists skill files as collateral.
2. `tg edit-ready … --json` → ticket T1 (base_sha+fingerprint; allowed files + primary span; CI-held escrow key).
3. Agent edits only those files. `tg verify-edit --ticket T1 --json` → PASS (escrowed, fingerprint-matched) + ran pytest target.
4. `tg evidence emit --ticket T1 --sign`; `tg review-bundle create …` and CI `verify` on the PR head.
5. Sibling omega-* agent: `tg workspace callers … --across dependents` → "no consumers parse scan_limit for this path anymore."

---

## Gate policy (council-amended)

- **H1, S1-Python, S2, S3, S6, S7-Python** locally gateable, WITH both-front-door + parity
  registration folded into every slice that adds a command (4-site law) — "no core-rust logic" never
  means "no native touch."
- **S1-native, S7-native** (rust_core/main.rs, path_domain.rs, tests/e2e) stay CI/cloud-only per the
  shared-box Rust ban; do NOT claim shipped until CI green.
- **S5 rides S1**; `--require-verify-edit` + SHA-field leg are additive Python after S1.
- **GPU (#169)** + CEO gates (#48/#72/#77/#131) untouched.
- **Drain/WIP:** unchanged one-merge-per-publish; do not stack >5 undrained PRs.
- **Ordering:** H1 (trust hygiene) → S1+S5 (the moat bundle) → S2 (differentiator) → S4 → S6/S3 → S7.

**Next step per repo process:** thinktank design council on the open design questions (S1 escrow-key
provenance, S2 score sources, S4 session schema), then each build item through
`tensor-grep-change-control` + TDD, one per iteration.