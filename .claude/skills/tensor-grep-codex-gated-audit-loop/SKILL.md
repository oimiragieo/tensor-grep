---
name: tensor-grep-codex-gated-audit-loop
description: >-
  Use when shipping an AUDIT-FIX PR for a verified H/M finding, running or resuming the
  codex-gated adversarial audit loop for a fix (behavioral RED -> minimal fix -> independent codex
  gate -> verify every finding with your OWN probes -> re-audit until SHIP), writing a gated test
  that must pass identically on the dev desktop AND the CI pytest env, or any security-surface
  change that needs an adversarial gate before merge (A3). Also when a prior fix to the same
  finding already shipped wrong (the twin law). Triggers: "codex-gated", "audit loop",
  "FIX-BEFORE-MERGE", "SHIP", "audit-fix PR", "adversarial gate", "try to BREAK it". Distinct from
  the pre-build plan council (tensor-grep-backlog-campaign) and the change gates
  (tensor-grep-change-control): this is the per-item fix loop itself. Not for first-pass
  feature planning or merge/release administration.
---

# tensor-grep: the codex-gated adversarial audit loop

The per-item **audit-fix loop** the 2026-08-08/09 wave validated on H2 (#979), M1 (#982), M3
(#983), and M14 (#984): verify the seam on `origin/main`, write a behavioral RED that proves the
defect, apply the minimal fix, run an **independent codex challenger**, treat every finding as a
hypothesis, verify it yourself, fix, re-audit until SHIP.

**Core principle:** codex is an independent *challenger*, never an oracle. Its verdicts are
hypotheses; the only authority is your own probes run against the real artifact. The loop exists
because every gate round this wave found real defects — a vacuous guard, a bypassed validator, an
env-dependent ratchet — that the build agent's own checks did not.

## When to use this skill vs a sibling

| Your task | Use |
|---|---|
| Fixing a verified audit finding (H/M) with a RED→GREEN loop + codex gate | **this skill** |
| Deciding WHICH backlog items to work, pre-build plan council, SPEC/TDD planning | `tensor-grep-backlog-campaign` |
| The non-negotiable merge/release/registration gates ("is this allowed to land?") | `tensor-grep-change-control` |
| Diagnosing a live bug/test failure / CI red | `tensor-grep-debugging-playbook` |
| Deciding what counts as proof for a test (TDD, oracles, red-green baselines) | `tensor-grep-validation-and-qa` |

**No skill routes around change-control.** This skill assumes the change-control gates still apply
in full at every merge; it is the loop that produces the diff the gates judge.

---

## The loop shape (per item)

- [ ] **Step 0 — Re-derive the finding on `origin/main`.** `git show origin/main:path` (never the
  dirty local tree — the working tree drifts and lies; only `git show` on the ref proves the
  seam). Confirm the defective line/symbol still exists and the defect still reproduces; a finding
  with no remaining reproducible mechanism is a RETIRED finding, not a fix target. Cite the seam
  by SYMBOL, not line (the never-re-stamp law): the verifying command is
  `git show origin/main:src/tensor_grep/cli/main.py | rg -n "def _the_symbol"`.
- [ ] **Step 1 — Behavioral RED test.** Write the test FIRST, run it against the un-fixed code,
  and require it to FAIL for the exact reason the finding names (A61 — pin the expected
  refusal/reason class; a RED that fails on crash/import error/hang proves nothing). A green
  pre-fix arm means the test does not test the defect.
- [ ] **Step 2 — Minimal GREEN fix.** Smallest defensible change; rerun the RED test → green, and
  the narrowed suite.
- [ ] **Step 3 — Independent codex gate.** Dispatch codex on a FRESH context, audit-only, with the
  verbatim brief: *"try to BREAK it, cite `file:line` for every claim, default FIX-FIRST when
  uncertain."* Give it the diff, the RED/GREEN tests, and the finding, but no build context — its
  job is to attack, not to confirm. (Repo A3/A18: a separate, independently-framed challenger is
  the only gate that counts; the build agent's own review is a hypothesis.)
- [ ] **Step 4 — Every finding is a HYPOTHESIS.** Codex's severity list is a shopping list, not a
  verdict. For EACH finding: re-derive the cited `file:line` on `origin/main`, write or run your
  OWN probe (never reuse codex's probe or the agent's probe — both share the author-perspective
  blind spot), and classify CONFIRMED / REFUTED / NEEDS-FIX-DECISION yourself. A finding that
  does not survive your probe is dropped with the probe's output recorded.
- [ ] **Step 5 — Fix + re-audit.** Fold CONFIRMED findings into the SAME PR while it is draft
  (A19: safety/honesty nits fold pre-merge; only cosmetic ones bank). Re-run the RED/GREEN arms,
  then dispatch codex round N+1 on the new diff. Stop when a round returns only
  APPROVE / SHIP (or nits with no behavioral change), and every finding from every intermediate
  round is closed with evidence.
- [ ] **Step 6 — Record the rounds in the commit message.** Each round: `Codex R<N> <verdict>
  (count: the N findings, each with one-line mechanism)` — the merge message is the durable
  audit trail. Then the normal change-control gates (draft PR, real venv re-verify, CI, human
  merge) apply unchanged.

The 2026-08-08/09 receipts for what "SHIP" actually took: H2 = R1(4)→R2(2)→R3(1)→R4(1)→R5
APPROVE-WITH-NITS; M1 = R1 FIX-BEFORE-MERGE(4)→R2 SHIP; M3 = R1(3)→R2(3)→R3(1)→R4 seat FAILED
(A10/A74, substituted with the orchestrator's own probes)→SHIP; M14 =
R1(3)→R2(3 harness defects)→R3 SHIP. Plan on 2–5 codex rounds per security-surface fix; a
round that returns zero findings on round 1 is the outlier, and is itself worth a second look.

---

## Environment-independence of gated tests (the 2026-08-09 mechanism)

**Any test that must pass on BOTH the dev desktop AND the CI pytest env must be env-independent
BY CONSTRUCTION — a test that passes locally and fails CI on a missing engine is a DEFECT in
the test, not the product.**

The CI pytest env lacks the optional engines the dev desktop has; a gated test that reaches a
tool's success arm through a real engine flips its verdict between the two environments:

| Engine | Dev desktop | CI pytest env | What flips |
|---|---|---|---|
| ast-grep binary | present | absent | AST tool success arms → absent-dep raise or "unavailable" envelope |
| tree-sitter native grammars | present | absent | `tg_ast_search`/`tg_ruleset_scan`/`tg_scan` success reach |
| dense model (model2vec) | present/absent/corrupt | absent | `tg_find` success arm (dense → BM25 fallback) |
| compiled `rust_core` extension | present (built) | absent | extension-backed probes raise instead of returning |

Receipt: the M14 contract-stamp census (PR #984) **failed on every CI test-python lane** because
the AST tools could not reach a success arm there; the fix was to drive exactly those
(tool, family) probes through a controlled AST engine seam so the tools' real success return
sites are value-checked identically everywhere.

**The mechanism — prefer hermetic forcing over env-detect:**

1. **Force a controlled deterministic seam for the optional engine** instead of detecting the
   environment:
   - dense leg: force `DenseUnavailableError` (`src/tensor_grep/core/retrieval_dense.py:52`) for
     the census duration — the deterministic BM25-only fallback success arm fires everywhere;
   - AST tools: shim the engine seam — `Pipeline.get_backend`
     (`src/tensor_grep/core/pipeline.py:448`) → a fixed `AstBackend` stub, and
     `_run_ast_scan_payload` (`src/tensor_grep/cli/main.py:6554`) → a deterministic
     empty-findings payload — so the tool's REAL success return site (the
     `_inject_mcp_contract_fields` envelope, `src/tensor_grep/cli/mcp_server.py:1125`) is
     exercised on every env.
2. **Keep error arms real.** Engine-free refusals (e.g. out-of-root confinement) run the real
   code and stay value-checked on both envs.
3. **Proof tests simulate the hostile envs.** A test that re-runs the census under a simulated
   no-ast-engine / corrupt-dense env and asserts the verdict is UNCHANGED is what proves the
   forcing worked — without it, the shim is just another unverified assumption.
4. **Allowlist with a TYPED expected-exception reason only when forcing is infeasible.** Each
   allowlisted (tool, family) entry names the EXACT exception types the absent-dep code path
   raises — the entry excuses ONLY those types, never "any error".

Example (adapted from the M14 census, `_AST_ENGINE_SHIM_FAMILIES`):

```python
# These (tool, family) probes need the controlled AST engine seam: their success
# arms require a real AST engine the CI pytest env does not install, so success
# reach would flip the census verdict between desktop and CI. The shim's own
# __name__ is load-bearing: tg_ast_search refuses any backend not named AstBackend.
_AST_ENGINE_SHIM_FAMILIES: dict[str, frozenset[str]] = {
    "tg_ast_search": frozenset({"curated", "schema"}),
    "tg_ruleset_scan": frozenset({"curated"}),
    "tg_scan": frozenset({"curated"}),
}


class _ControlledAstBackendShim:
    """Fixed 'AstBackend' identity, empty results, no engine dependency."""

    def search(
        self, current_file: str, pattern: str, *, config: object | None = None
    ) -> SearchResult:
        return SearchResult(matches=[], total_files=0, total_matches=0)


_ControlledAstBackendShim.__name__ = "AstBackend"
```

**Mutation-control: a ratchet that cannot RED is decoration.** Before trusting any census-style
gated test, prove it goes RED on each of these mutations — and re-point the ratchet until it
does (this is Form 1 applied to guards, from `AGENTS.md`):

- [ ] **Deleting a member** from the census/set/population → RED;
- [ ] **Removing a stamped path** (a wrapped caller, an envelope field) from a covered member → RED;
- [ ] **An allowlisted family raising an unexpected exception type** — the allowlist must be
  typed; a bare "any error is fine" entry masks the exact regression it exists to catch → RED.

---

## Attribution discipline

- **Verify subagent claims yourself.** Run the tests, read the diff, probe the behavior. A
  worktree agent's "tests pass" is un-runnable in its own tree (no venv) and is a hypothesis
  until re-run in the real venv; codex's self-verification is likewise not yours. The H2 R4
  receipt: codex found the pinning test was VACUOUS under `CliRunner`'s `sys.argv` — only a
  stubbed `sys.argv` plus a bidirectional control arm actually exercised the exemption.
- **Attribute the CI decode to the exact artifact + SHA (A44).** "CI red" is not one thing:
  name the run, the job, the commit that ran, and what the failing STEP said. A local green on a
  different SHA clears nothing.
- **Prove "pre-existing environmental failure" on the pristine base.** Before calling a failing
  test environmental (e.g. "worktree lacks the compiled `rust_core` ext"), run the failing test
  against the un-changed `origin/main` artifact and capture the identical failure. M14 receipt:
  "4 `*rewrite*embedded*` env failures proven pre-existing on origin/main" — the proof, not the
  claim, is what unblocks.
- **A no-verdict codex seat is a FAILED seat, not approval and not a blocker (A10/A74).** When
  the seat dies on a content filter / auth spin, substitute the orchestrator's own probes and
  record the substitution; the draft-PR gate + CI remain the durable arbiter.

---

## Cross-verification receipts (why we do the probes ourselves)

Each named receipt is a round where codex (or CI) made a claim the loop then had to verify —
and where trusting the claim instead of probing would have shipped the defect round.

**1. M3 — a vacuous guard, then three codex-invented attacks.** The finding itself was a
vacuity: `_workspace_edit_target_uris` (`src/tensor_grep/cli/lsp_server.py:246`) collected only
changes-map keys + `textDocument.uri`, so `CreateFile`/`RenameFile`/`DeleteFile` members (which
carry `uri`/`oldUri`/`newUri`, no `textDocument`) yielded `[]` and the guard
`if edit_uris and all(...)` passed VACUOUSLY — out-of-root file-ops forwarded to the IDE. Worse
than unchecked: an empty guarantee that read as a check. The fix enumerated all five target
fields via `_document_change_member_targets` (`lsp_server.py:200`) and composed a two-net refusal
(`_workspace_edit_refused`, `lsp_server.py:298`: per-target validator AND resolve+containment).
Codex rounds then invented the attack classes one at a time — R2: a `kind`-null member, a
`snake_case document_changes` key the lsprotocol constructor accepts (outbound bypass), and
`file:/`-style RFC-8089 forms; R3: path-rootless `file:C:evil`, which previously resolved
against the server's per-drive CWD and passed in-root while the original string was forwarded
unchanged. Round 4's seat FAILED on its content filter — the orchestrator's own probes verified
all seven hostile forms resolve out-of-root and refuse. Every invented form was folded into
`_valid_external_document_uri` (`lsp_server.py:135`).

**2. M14 — the census correcting itself: "15/58 approx" → live 19 sites / 11 tools → 0.** The
fix commit's own first census claim ("15/58 approx" unstamped) was overstated; the live,
registry-derived census (58 tools × success+error families from `mcp.list_tools`, never a hand
list) corrected it to 19 real sites across 11 tools with MASKED success paths — the
masked-success arm was the real class, invisible to a hand-written count. After
`_inject_mcp_contract_fields` (`src/tensor_grep/cli/mcp_server.py:1125`) hard-assigned the central
`mcp_contract_version` const (was `setdefault`, so a tool's own stale/forked literal won), the
value ratchet's violation count reached 0 at every (tool, family) — and codex R2 then found
three HARNESS defects in the ratchet itself (an exception-allowlist masking real failures, an
env-dependence on the dense model — the 2026-08-09 mechanism above — and a partial-key parity
gap). The ratchet, not the fix, was the part that needed two more rounds. `_envelope_base`
(`mcp_server.py:695`) is the central stamp helper the const is threaded through.

**3. H2 — the front-door-rewrite shadow.** `SEARCH_OPTION_FIRST_FLAGS`
(`rust_core/src/main.rs:104`) includes `--count-matches`, so the positional
`tg PAT . --gpu-device-ids 0 --count-matches` is REWRITTEN into the search-subcommand form by
`normalize_top_level_search_args` (`rust_core/src/main.rs:1785`) and never reaches
`run_positional_cli`'s `validate_positional_native_structured_refusals`
(`rust_core/src/main.rs:8861`) — the validator was shadowed by the front-door rewrite. On the
search path the count/files flags fall through `search_requires_ripgrep_passthrough`
(`rust_core/src/main.rs:8722`)'s `!json && !ndjson` gate, so with `rg` present the explicit GPU
request was SILENTLY DROPPED (exit 0, wrong output); with `rg` absent the pre-existing
rg-required gate in `handle_ripgrep_search` (`rust_core/src/main.rs:9502`) exited 2 by accident
with the wrong wording. The fix was a search-form gate in `handle_ripgrep_search` BEFORE the
rg-passthrough early return — an airtight-ordering argument: for these combos it is the first
gate in BOTH environments, so the message becomes deterministic rather than dual-env-tolerated.
Lesson: a validator is only as real as the doors that actually reach it — census the ROUTES
before trusting the guard.

**4. M3 cross-platform — the fix's escape hatch was Windows-only.** The drive-absolute URI strip
ran unconditionally: on POSIX the root-anchored `/C:/Windows/evil` became a RELATIVE
`C:/Windows/evil` that resolved INSIDE the process cwd — recreating the drive-relative escape
the fix was meant to close, on Linux instead of Windows (Linux CI reddened
`test_uri_to_path_handles_single_slash_file_uri`). The fix was gating the strip on
`os.name == "nt"`. This is the environmental twin of receipt 3: verification on the dev desktop
alone cannot see the second environment's behavior — that is what CI is for, and why the gated
test's env-independence matters.

---

## External anchors (Exa research, 2026-08-09)

The loop is this project's instance of a family of adversarial review loops; the family
validates the shape, ours is tuned to this repo's evidence bar (real tests as the "prove"
execution, `file:line`-cited findings, round-recording commit messages).

| Anchor | Their loop | This skill's mapping |
|---|---|---|
| **ultra-review** (github.com/alexrolls/ultra-review) | Fleet of narrow-scope reviewers: R1 discover in parallel → R2 cross-examine (each tries to DISPROVE peers' findings; survivors are the defensible ones) → R3 survivors strengthen with concrete fixes → R4 digest; the orchestrator never writes findings; heterogeneous-LLM friendly (Claude + Codex mix). | Codex is our single independent challenger per round (fleet of one — cheaper for a per-item PR); "adversarially confirmed signal" = a finding that survives OUR re-verification, the same survival test applied by a second model; cross-vendor (OpenAI codex vs Anthropic us) gives the heterogeneity. |
| **AWS Builder Center "a multi-agent security review loop: find, challenge, prove, and fix"** (builder.aws.com/content/3EfBZG9YPytyxOjWOXKhUpFw9cl) | Find (Claude) → Challenge (Codex read-only: "reasons it should not ship, not confirm it looks fine") → Prove (AgentCore sandbox PoC; "a finding from one model is only a hypothesis until a second model, and ideally a real execution, backs it up"; non-reproducing findings set aside for a human) → Patch (Codex writes) → Claude reviews the patch and adjusts → Re-verify (same PoC in a fresh sandbox). Two model vendors; repro-before-fix. | Challenge = our codex gate with the identical "try to BREAK it" framing; Prove = our behavioral RED→GREEN in the repo's real venv/CI (execution, not opinion) — we do not need a sandbox because the tests ARE the execution; Patch+review = codex proposes, WE write/verify the fix (reverse of AWS's roles, same maker-checker separation); repro-before-fix = Step 0's show-the-seam + Step 1's RED. |
| **ASDLC.io Adversarial Code Review** (asdlc.io/patterns/adversarial-code-review) | Maker-checker separation: Builder (throughput) vs Critic (high-reasoning, must start a NEW session/thread so it evaluates only artifacts, not the builder's reasoning); critic lanes; critics emit PASS-or-violations, never alternative implementations; Review Gate (probabilistic, adversarial) vs Quality Gate (deterministic: syntax/compile/lint/test) vs Acceptance Gate (subjective HITL); negation blindness ⇒ adversarial review needs deterministic backing. | Codex = the Critic in a fresh context, audit-only, verdict-shaped (`SHIP` \| `FIX-BEFORE-MERGE(must-fix list)`), never writing the fix; our RED/GREEN tests = the deterministic Quality Gate backing the probabilistic Review Gate (the exact "negation blindness needs deterministic backing" doctrine); the human merge = the Acceptance Gate. |
| **Augment Code "Adversarial Code Review: Why the Maker Shouldn't Grade the Checker"** (augmentcode.com/guides/adversarial-code-review) | Fresh-context reviewer with read-only tools (Read/Grep/Glob only) and a pinned, ideally different model family; a SEPARATE fixer applies corrections; new reviewer re-passes the changed diff; rollout starts ADVISORY and only converts to blocking gates on critical findings; permission-level (not prompt-level) authority scoping. | Codex is audit-only by construction — it never edits the tree in the loop; we are the separate fixer; "run advisory first, then gate" = findings are hypotheses (Step 4) until our probes confirm them, and only the draft-PR human merge is a hard gate; pinned different family = codex (OpenAI) reviewing Anthropic-authored diffs. |

---

## Common traps (each cost a round in the 2026-08-08/09 wave)

- **Trusting codex's verdict without re-verification.** A finding that reads well is still a
  hypothesis. Re-derive the `file:line` on `origin/main` and run your own probe (Step 4) — every
  one of the wave's fixes was gate-corrected, and most of those corrections came from OUR probes
  or CI, not from codex's first verdict.
- **Citing the dirty local tree.** `git show origin/main:path` is the only seam authority; the
  working tree drifts, worktrees are stale by definition, and a "defect" that resolves cleanly
  against the local checkout is not a defect.
- **A "RED" that is not behavioral.** If the new test fails pre-fix only on import/panic/setup,
  or — worse — passes pre-fix, it proves nothing about the fix. Require the named refusal/semantic
  to fail, and record the pre-fix failure output.
- **An env-dependent gated test.** The 2026-08-09 lesson: the test is the DEFECT when it flips
  verdicts between desktop and CI on a missing optional engine. Force the seam; prove the force;
  allowlist typed exceptions only.
- **A ratchet that cannot RED on mutation.** Deleting a member, a stamped path, or an
  unexpected exception type must each go RED — else the ratchet masks the regression it exists
  to catch (and codex R2 found exactly this on M14).
- **A test built at the wrong seam.** A probe that builds via the same helper being fixed (H2
  R4's vacuous `CliRunner` pin) is structurally incapable of failing; capture at the seam the
  VALUE crosses.
- **Waiting on or trusting a failed codex seat.** A seat that dies mid-round is FAILED (A10/A74).
  Substitute your own probes, record the substitution, keep the draft-PR + CI as the arbiter —
  never treat a failed seat as approval, and never let it block.
- **Not recording the rounds.** The commit message is the durable audit trail. Rounds without
  severity+file:line in the message read as "fixed once" — the exact claim the loop disproves.
- **GPU/unsupported-engine claims.** A count/files + `--gpu-device-ids` combo that did not exit 2
  with a naming message is still open — the H2 gate contract. The exit code and message ARE the
  fix; do not accept "rg was absent so it exited 2 by accident" as compliance.

## Quick reference

```
Step 0  git show origin/main:<file> | rg -n "def <symbol>"     # seam must exist there
Step 1  behavioral RED test -> must fail for the NAMED reason pre-fix
Step 2  minimal GREEN fix -> targeted suite green
Step 3  codex gate: "try to BREAK it, cite file:line, FIX-FIRST when uncertain" (fresh ctx)
Step 4  every finding -> your own probe -> CONFIRMED/REFUTED/NEEDS-FIX-DECISION
Step 5  fold confirmed fixes in-draft -> codex round N+1 -> repeat until SHIP
Step 6  commit message records each round (severity + mechanism) -> normal gates -> draft PR
```

The endpoint is always a **draft PR** a human merges; the codex gate is an addition to the
change-control gates, never a substitute for them.