# Design-doc convention

This file exists because an audit found tensor-grep had no written convention for what belongs in
a design doc under `docs/design/` — the five docs already there (`2026-08-13-backlog-completion.md`,
`2026-08-13-replace-in-place-symlink-threat-model.md`, `2026-08-19-split-floor-escape.md`,
`2026-08-20-route-a-adversarial-review.md`, `dd006-accept-side-bound.md`) each independently
converged on the same shape. This doc names that shape so the next one does not have to
re-derive it, and so a reviewer has something concrete to check a new design doc against.

It is descriptive, not invented: every rule below is backed by a citation to one of the five docs
practicing it. Where they disagree in form (e.g. a bare bullet list vs. a table), pick whichever
reads better for the doc at hand — the *content* obligations are what's load-bearing, not the
markdown shape.

## 1. Filename and status line

Name the file `docs/design/YYYY-MM-DD-<short-slug>.md`, dated the day the doc is first published
(not the day the feature ships). A doc that reviews an earlier design (like
`2026-08-20-route-a-adversarial-review.md` reviewing `2026-08-19-split-floor-escape.md`) gets its
own date and names what it reviews in its first lines.

Every doc opens with a status block — a table (`dd006-accept-side-bound.md`) or a blockquote
(`2026-08-19-split-floor-escape.md`, `2026-08-13-backlog-completion.md`,
`2026-08-13-replace-in-place-symlink-threat-model.md`) — that states, in one place a reader does
not have to hunt for:

- **What phase this is.** `2026-08-13-backlog-completion.md`: *"Phase 1 (requirements/design)
  artifact... no code was written, no tests run, no PR opened."* `dd006-accept-side-bound.md`:
  *"Status: DRAFT — no product code in this packet."*
- **What base commit or tree every code citation was verified against.**
  `2026-08-13-replace-in-place-symlink-threat-model.md`: *"Base: `origin/main`
  `c04fccf44ee7f3efd2294eadf00a8578b53bbe06` (2026-08-13). All code citations verified against
  this tree."* This is not decoration — without a pinned base, a reader cannot tell whether a
  citation rotted the next day, and per AGENTS.md's dated-instrument laws a citation that still
  *resolves* is not proof it still says what the doc claims.
- **Whether the doc has been reviewed/approved, and by what.** `2026-08-19-split-floor-escape.md`:
  *"the CONVERSION (steps 2-4) is still a proposal and still **unreviewed** — it needs the
  adversarial review named in §5 before any call site changes."* A status line that omits review
  state invites a reader to treat DRAFT as SHIPPED.

## 2. What it decides — say it in one paragraph, up front

State the actual decision as a declarative sentence before any evidence. `2026-08-19-split-floor-
escape.md`: *"**Decides:** how `cli/main.py`, `cli/repo_map.py` and `cli/mcp_server.py` reach the
1,500-line limit, given that **they cannot get there by moving code**."* A reader who stops after
the status block and this paragraph should already know what the doc is arguing for.

## 3. Measured evidence, not assertion

Every claim of the shape "X is faster / cheaper / safer / correct" is backed by a real command a
reader can re-run, not a description of what running it would show. `2026-08-19-split-floor-
escape.md` cites `scripts/measure_split_floor.py` and `scripts/cost_split_floor_routes.py` by
name and reproduces their table output; `2026-08-20-route-a-adversarial-review.md` walks every
`ast.Call` site by hand and reports a table with row totals that sum to the total claimed.
`2026-08-13-replace-in-place-symlink-threat-model.md` cites exact `file:line` for every behavioral
claim about `rust_core/src/backend_cpu.rs` and separates "reachability, stated honestly" (no `tg`
CLI caller exists yet) from the abstract threat class, so the doc cannot be misread as describing
a live exploit.

House rule this doc adds explicitly (not yet violated in the five examples, but implied by
AGENTS.md's evidence laws): **a table of measured numbers states the tool that produced them**, so
a future correction can say which tool was wrong instead of only which number was wrong (see §5).

## 4. What it explicitly does NOT decide

Name the boundary of the decision as clearly as the decision itself.
`2026-08-20-route-a-adversarial-review.md`'s opening lines are the cleanest example: *"Scope,
stated up front so it is not over-read. This is a STATIC review... It is not a multi-seat council,
and it does not clear the runtime cost."* `2026-08-19-split-floor-escape.md` §5 has a "what NOT to
build" subsection for the same reason `prepare_service.py`'s ledger-claim comment does — an
unscoped design invites the next reader to assume it settled more than it did.

## 5. Corrections are recorded in place, never silently

This is the single most load-bearing convention in the library, and the reason
`2026-08-19-split-floor-escape.md` is the reference example named in this repo's task brief for
this doc. When a design doc's own measured numbers turn out to be wrong:

1. **Leave the original wrong numbers visible.** Do not delete or edit them in place — a reader
   who only saw the corrected version has no way to know the tool that produced the wrong numbers
   was ever trusted, and cannot learn from the failure mode.
2. **Prefix the correction with `> **CORRECTED <date>...**`** as its own blockquote, placed
   immediately after the table or claim it corrects — not batched into a changelog at the bottom
   of the file. `2026-08-19-split-floor-escape.md` has two such corrections in one doc (the
   split-floor table's `agent_capsule.py` undercounted at 1,190 vs. the true 1,527; the
   route-costing table read a hardcoded-path tree instead of the real one).
3. **State the root cause of the wrong number, not just the new number.** *"The first version of
   this table read 11,025 / 9,453 / 5,554 / **1,190**, from a tool that omitted the most obvious
   members of the locked set: **the patched functions themselves**."* A correction that only
   swaps in a new number without explaining the mechanism is not verifiably fixed — the reader
   cannot tell whether the same bug produced the correction too.
4. **State which direction the error ran, and whether that direction was dangerous.** *"The error
   ran in the **dangerous direction**: a too-low floor reads as permission to split... Only the
   tool's stated 'this is a lower bound' kept that from being a wasted wave."* This is the
   general form of the CLAUDE.md rule "state which way your tool errs, in the tool" — apply it to
   corrections too.
5. **State whether the correction changes the doc's recommendation.** *"The recommendation is
   unaffected — Route A still wins ~2x."* A correction that silently leaves the reader to
   re-derive whether the bottom-line verdict moved is an incomplete correction.

A design doc with zero corrections over its life is not necessarily one that was right the first
time — it may be one nobody re-measured. Do not treat an uncorrected doc as stronger evidence than
a corrected one; treat a *documented* correction as evidence the doc is being actively checked.

## 6. House style carried over from the rest of the repo

- **ASCII-only.** `2026-08-13-backlog-completion.md`: *"ASCII-only by construction (A96:
  non-ASCII punctuation in governed docs defeats byte-exact edit-tool matches)."* Applies to every
  doc under `docs/design/`, not just that one.
- **Cite the symbol, not just the line**, per this repo's own rule (`AGENTS.md`, "Cite the SYMBOL,
  not the line" / `CLAUDE.md`'s skill-index section): a line number rots as the file is edited; a
  `grep -n 'def some_symbol' path` command a reader can run themselves does not. Prefer that form
  when the citation is meant to outlive the current diff.
- **Verify claims against the real code before writing them, not from memory or a prior doc.**
  Every one of the five example docs opens with a base-commit pin specifically to make this
  checkable — see §1.

## 7. Relationship to plans, decisions, and requirements

`docs/design/` sits beside `docs/plans/`, `docs/decisions/`, and `docs/requirements/`. The
convention observed across the five examples: a design doc is the *argument* for why a shape is
correct (with evidence); a plan (`docs/plans/`) is the *execution* sequence built on that argument
(`2026-08-13-backlog-completion.md`: *"This document is the argument; the plan is the execution."*);
`docs/decisions/` and `docs/requirements/` (see `dd006-accept-side-bound.md`'s header table) hold
the narrower, more durable artifacts a design doc's Phase 1 work feeds. Link the companion plan or
decision doc from the status block, both directions, so a reader who lands on either can find the
other.
