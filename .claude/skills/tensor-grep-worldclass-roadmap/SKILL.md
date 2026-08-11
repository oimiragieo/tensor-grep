---
name: tensor-grep-worldclass-roadmap
description: >-
  Use when planning, building, reviewing, or prioritizing any slice of the world-class roadmap —
  the agentic EDIT-CONTROL PLANE thesis (not faster grep); the S1 fail-closed edit tickets +
  verify-edit with ESCROWED CI-held-key evidence; S2 registration-aware impact-diff; S3 next_action
  protocol; S4 warm-session resume; S5 head-bound escrowed evidence chain; S6 semantic default +
  why_ranked; S7 federated workspace object; or the H1 unknown-command/PATH-honesty prerequisite.
  Triggers: "edit-control plane", "world-class", "verify-edit", "escrowed evidence", "S1..S7",
  "edit tickets", "CI-held signing key", "semantic blast radius", "impact diff", "next_action
  protocol", "federated workspace", "why_ranked". Covers the council-amended spine (design +
  prioritization contracts), the banked-vs-new status of each item, the both-front-door + 4-site
  registration obligations, and the gate sequence (change-control → thinktank design council → TDD)
  every slice must pass. NOT a build plan — build routing happens through
  tensor-grep-change-control; the canonical source contract is docs/plans/2026-08-09-worldclass-roadmap.md.
---

# tensor-grep: world-class roadmap (edit-control plane)

The moat is the **agentic edit-control plane** — deciding what to touch, what else breaks, what to
run, and when to stop, with receipts — not faster grep. The expensive 80% (evidence emit/verify,
review-bundle, ledger, checkpoints) is already shipped; the remaining rails exceed what any agent
execution sandbox can do:

1. **semantic blast-radius enforcement** (what must change when this changes),
2. **primary-span claim-checking** (did the edit touch the span the ticket authorized),
3. **escrowed, head-bound evidence** (receipts a CI-held key signs, not the editing agent's).

An agent's sandbox already enforces "touched files ⊆ allowed" natively — that is **table stakes,
not a PASS condition** (council HIGH-1).

## When NOT to use this skill

| Situation | Use instead |
|---|---|
| Building/merging any concrete slice | `tensor-grep-change-control` (gates, 4-site registration, TDD) |
| The roadmap's contracts went stale | re-verify against `docs/plans/2026-08-09-worldclass-roadmap.md` + `tensor-grep-release-drift-check` |
| Evidence signing/verification mechanics | `tensor-grep-enterprise-review-bundle` (trusted-key/require-trusted family) |
| Agent-facing search rank/semantics | `tensor-grep-find-and-route`, `tensor-grep-semantic-search-campaign` |
| Roadmap demand/business framing | `docs/audits/2026-08-06-ceo-gated-recommendation-packets.md` (CEO-gated) |

---

## Status ledger (banked vs new — re-derive from the plan doc, never from memory)

| Item | Status | One-line contract |
|---|---|---|
| **H1** unknown-command fail-closed + PATH honesty | **SHIPPED** (v1.110.13 unknown_command A90; v1.110.14 doctor schema-3 `pypi_latest`/`shadow_launchers`/`installation_health`) | both front doors exit 2 + `nearest[]`; doctor surfaces PATH-vs-claimed distribution |
| **S1** fail-closed edit tickets + verify-edit, escrowed | **NEW (re-scoped; THE moat)** | See the S1 contract below — nothing ships until this does |
| **S2** registration-aware impact-diff | NEW | changed_symbols + registration_sites (allowlists, `@router`, `Commands::`, class registries) + tests_to_run |
| **S3** next_action machine protocol + budget envelope | NEW (default-OFF additive) | capsule extension; budget envelope for the machine consumer |
| **S4** warm session resume of decisions | BANKED PARTIALLY | correct the premise before building (daemon holds a symbol map, not a search index) |
| **S5** head-bound, escrowed evidence chain at CI | BANKED PARTIALLY | rides S1's escrow; receipts pinned to base_sha + fingerprint |
| **S6** semantic default + why_ranked | BANKED PARTIALLY | additive find UX: `why_ranked` reasons + `install_state` |
| **S7** federated workspace object | BANKED F8 | Python slice first, rust/e2e later |

## S1 — the load-bearing contract (verify-edit, escrowed)

- `ticket_id { base_sha, working_tree_fingerprint, primary_span, allowed_files[], validation_commands[], expires_at, ask_user_before_editing }`.
- Verdict ∈ {`PASS`, `FAIL(edit_contract_violated)`, `UNVERIFIED(reason)`}.
- **PASS requires ESCROWED evidence**: validation subprocess stdout-hash + exit code + duration,
  signed by a key pinned via `TG_EVIDENCE_TRUSTED_KEYS` that the **editing principal does NOT hold**
  (CI-held); primary span changed as claimed; no silent extras outside `blast_radius_floor`;
  tree fingerprint matches the ticket (no drift from sibling agents/rebase). Self-attestation by the
  editing agent is Oracle Form 8 (split-oracle) — never a PASS.
- **FAIL closed**: `incomplete_reason_class=edit_contract_violated`, exit 2, revert-eligible file set
  (ticket carries checkpoint_id; rollback-on-violation is the un-apply primitive).
- **UNVERIFIED** (runner-less repo / deadline-truncated validation / escrow key absent): never
  certifies, distinct code, agent should ask the user.

External precedents that validate the shape (Exa-verified 2026-08-11):
- **Occasio**: GitHub Actions OIDC-signed agent-run attestations (Sigstore/Rekor), offline-verifiable
  — the CI-held-key escrow is an established pattern, not an invented one.
- **AET (AdvancingTitans/agent-engineering-toolkit)**: an evidence plane with PASS/FAIL/UNKNOWN +
  freshness states (`EXACT_MATCH` → `HEAD_CHANGED_RELEVANT_FILES_MATCH` → `UNKNOWN`) and
  freshness-stops — the same fail-closed-on-drift discipline as the ticket fingerprint.
- **Anthropic harness papers** (building-effective-agents; effective-harnesses; harness-design): the
  default-FAIL contract (evidence before claiming done), fresh-context evaluator, and
  verify-before-claim patterns are the general form of S1's verdict staging.
- **Agent Edit Contract** (zemna.net 2026-07): repo map → task boundary → verification command
  named BEFORE the edit → artifact proof (handle, freshness, minimum substance, domain assertion) →
  rollback note. S1's ticket is this contract made machine-checkable (see `docs/plans/` for the
  source contract claims).

## Both-front-door + registration obligations (A90/A91)

Every S1–S7 slice that adds a command or flag MUST enroll: `KNOWN_COMMANDS` (`commands.py`),
`Commands::X` + dispatch arm in `rust_core/src/main.rs`, `PUBLIC_TOP_LEVEL_COMMANDS`
(`tests/e2e/test_routing_parity.py`), and the `@app.command` in `main.py`; search flags also need
`SEARCH_PYTHON_PASSTHROUGH_FLAGS` (rust) + `bootstrap._TG_ONLY_SEARCH_FLAGS` (python). Un-enrolled
stealth commands die at first dogfood with the A90 unknown-command refusal — the slice is then
honest only as "no core-rust logic," never "no native touch."

## Gate sequence for any roadmap slice

1. Scoped design + operating contract (plan doc style) — premises verified against origin/main (A75/A93).
2. Thinktank design council (ground-truth + adversarial + gate seats); council amendments folded in BEFORE build.
3. TDD-first build (worktree-isolated), real-venv re-verify, ruff/mypy, mandatory adversarial gate.
4. Both-front-door dogfood on the real published binary (A90 receipts as the first smoke).
5. `fix:`/`feat:` PR title so the release actually ships the slice (release class is part of the fix).

## Common mistakes

- Treating "touched files ⊆ allowed" (sandbox table stakes) as a PASS condition — S1's claim-check is
  about the PRIMARY SPAN, not the allowlist.
- Building S5 without S1 (escrow is S1's mechanism; S5 rides it).
- Re-deriving banked status from memory instead of the plan doc — the ledger above was verified
  2026-08-11 against the doc; re-check on any drift (A94 discipline).