# AGT-04 confined-walk design — independent security review (2026-09-12)

Reviews `docs/design/2026-09-10-agt04-symlink-junction-confinement.md`, which closes with a
Review gate requiring "an equivalent independent security review before any implementation PR
is opened against it. Not self-approved by the same session that wrote it."

**Reviewer:** a different session from the one that authored the design (authored 2026-09-10,
reviewed 2026-09-12). **Verdict: CHANGES_REQUIRED.** Four findings; F1 and F2 are blocking,
F3 is blocking unless explicitly scoped out, F4 is a correctness gap that will surface as
false FAILs in production if unaddressed.

New dated artifact rather than an edit to the 2026-09-10 doc: this repo's standing rule is
never to edit a dated receipt in place.

---

## F1 (BLOCKING) — items 1 and 3 contradict each other for an in-root directory symlink

Item 1: "A directory whose identity is a symlink/junction/reparse point is **excluded from
descent** by default ... fail-closed (exclude) is the correct default."

Item 3: "target-within-root → **follow and fingerprint normally**."

For a *directory* symlink whose target is inside the root, item 1 excludes it and item 3
follows it. The design does not say which rule wins, and a builder will resolve it
arbitrarily — in opposite directions on different call sites, most likely.

This matters beyond tidiness: if item 3 wins, a confined walk follows directory links, and the
whole confinement property rests on the target-classification in F2 being correct. If item 1
wins, item 3's "never refused and never followed for legitimate in-repo links" contract from
Part 3 is silently broken for directories.

**Required:** state the precedence explicitly, and state it per-kind (directory link vs leaf
file link). Recommend item 1 wins for DIRECTORIES (exclude from descent, record the reason)
and item 3 applies only to LEAF FILES. A directory link that is excluded from descent should
still be *reported*, so its contents are visibly absent rather than invisibly absent.

## F2 (BLOCKING) — item 3 specifies resolve-then-act, which is the TOCTOU shape this repo has laws against

Item 3: "The design must resolve the link's target and classify it: target-within-root → follow
and fingerprint normally; target-outside-root or unresolvable → exclude and report."

Resolving a path and then acting on it is a time-of-check/time-of-use window: between the
`resolve()` and the subsequent `open()`/`stat()`, the link can be repointed outside the root.
For an *edit-ticket population* this is not academic — the population defines what drift the
verifier can later detect, so a link flipped inside that window yields fingerprints for a tree
the ticket does not describe.

`.claude/skills/tensor-grep-cross-platform-path-confinement/SKILL.md` names exactly this
distinction ("handle-anchored identity versus resolve-then-act (TOCTOU)"), and AGENTS.md
carries it as a dated law. The design cites the 2026-08-13 replace-in-place threat model for
junction *detection* but does not inherit its identity discipline.

**Required:** either (a) specify handle-anchored identity — open the entry once and derive both
its identity and its content from that same handle — or (b) state plainly that this design
closes the *spelling/classification* gap and NOT the TOCTOU window, and record the residual in
`population_status`. Silence reads as a claim the window is closed. (Precedent for the honest
form: commit `9fbec9e` on `fix/edit-verify-root-binding` disclaims TOCTOU explicitly while
closing the path-spelling gap in `verify_edit_ticket`.)

## F3 (BLOCKING unless scoped out) — item 4 adds `git ls-files` as a trusted oracle without a threat model

Item 4 proposes preferring `git ls-files` as the population source, on the grounds that git's
tracked-file list "already excludes untracked symlink escapes by construction".

That is true of the *output*, but the proposal introduces a new and unanalyzed trust
dependency: it executes `git` as a subprocess with an attacker-influenceable repository as its
working directory. A repository's own `.git/config` can set values that cause git to execute
attacker-chosen commands (`core.fsmonitor`, `core.pager`, `core.sshCommand`, aliases), so
running git inside a repo one does not already trust is a recognized code-execution surface —
not a symlink question at all. `tg` is explicitly an agent-facing tool pointed at arbitrary
checkouts, which is exactly the population where this matters.

**Required:** either scope item 4 out of AGT-04 and file it separately with its own threat
model, or specify the hardening inline — at minimum `git --no-optional-locks ls-files -z`
executed with `GIT_CONFIG_GLOBAL=/dev/null`-equivalent isolation and `core.fsmonitor` disabled,
`-z` parsing (paths may contain newlines), and an explicit statement of what happens when git
exits non-zero (fail closed to the hardened walk, never fall through to an empty population).
An empty `git ls-files` result must never be read as "the repo has no files".

## F4 (CORRECTNESS) — no migration story for tickets minted under the current following behavior

The design states no `EditReadyTicketV1` schema bump is anticipated. Accepted — but changing
the population rule changes *which files are fingerprinted*, so a ticket minted before the
change and verified after it can see paths appear or disappear from the population for reasons
unrelated to any edit.

`verify_edit_ticket` compares `set(ticket.pre_edit_fingerprints) | set(current_fps)` and flags
any path whose fingerprints differ and which was not declared. A path that the old walk
followed (through a link) and the new walk excludes will have a pre-edit fingerprint and no
current one → **undeclared drift → false FAIL** on an untouched tree.

**Required:** state the intended behavior for pre-existing tickets. Options: stamp a population
*rule version* alongside `population_status` and refuse (not silently pass) tickets minted under
a different rule; or accept the false-FAIL and document it as a one-time cutover. Either is
defensible; leaving it unstated produces mystery FAILs that look like tampering.

---

## What the design gets right (not to be relitigated at implementation time)

- Fail-closed-by-default for directory descent (item 1) is the correct default for an
  edit-ticket population, and the doc says why it differs from a general-purpose walker.
- Deferring the exact Windows junction API to implementation *with a citation requirement*
  rather than re-litigating "are junctions symlinks" is the right call — that question has a
  bounded-probe receipt already.
- Item 5's single parametrized control matrix (existing budget controls PLUS new symlink rows)
  is the right shape; a separate suite per control is how one control silently regresses another.

## Disposition

**CHANGES_REQUIRED.** F1 and F2 must be resolved in the design before an implementation PR is
opened; F3 must be resolved or scoped out; F4 must be stated. F2's option (b) is acceptable —
an honest residual beats an implied guarantee.

This review is one independent pass, not a second one. The design doc's own gate names
"senior-software-architect + security-trust-officer, or an equivalent independent security
review"; a second reviewer disagreeing with any finding here should be treated as a real
disagreement to resolve, not as this review being overridden.
