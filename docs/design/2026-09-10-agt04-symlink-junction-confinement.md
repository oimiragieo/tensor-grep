# AGT-04 symlink/junction confined-primitive design (Task 04 remainder)

Source: `docs/plans/2026-09-07-agentic-quality-simplification.md` Task 04, remaining checkboxes
("git-tracked identity as primary population source", "reviewed design doc", "symlink/junction
confined-primitive controls", "full control-matrix re-run"). Scope: **design only** — this doc
names the concrete confinement contract for `edit_ticket_service.py`'s population walk before any
implementation touches it, per `tensor-grep-cross-platform-path-confinement`'s discipline. No code
changes ship in this slice.

## The concrete seam and why it needs a design doc, not a quick fix

`_walk_tracked_files_bounded` (`src/tensor_grep/cli/edit_ticket_service.py:87`) is the population
source for `EditReadyTicketV1.pre_edit_fingerprints` — the set of file contents `verify_edit_ticket`
(`edit_ticket_service.py:213`) later trusts to decide whether an edit ticket's population was
complete. AGT-04's shipped commit (`9377ea4`) already fixed the *bounded-budget* half (finite
file-count/byte limits, explicit `incomplete` reporting instead of silent truncation). It did not
touch the *identity* half:

- `os.walk(root)` with `dirnames[:] = [d for d in dirnames if d not in _IGNORED_DEPENDENCY_DIRS]`
  descends into every directory entry it sees, including a **junction** on Windows — per this
  skill's Part 1 table, a junction requires no privilege to create, `os.walk` traverses it as an
  ordinary directory, and (pre-toolchain-probe) `Path.is_symlink()` reports `False` on it. A
  junctioned subdirectory under `repo_root` is invisible to any leaf-symlink check and would be
  walked transparently.
- `item.stat()` (`edit_ticket_service.py:121`) follows symlinks by default (no
  `follow_symlinks=False`), so a symlinked *file* under root would have its out-of-root target's
  size and content fingerprinted as if it were in-root content.
- This is the exact M1 shape from Part 1 of the skill ("a junctioned ANCESTOR under root is
  traversed transparently and reads OUT-of-root content — invisible to a leaf-symlink check"),
  applied to a different consumer (edit-ticket population instead of checkpoint copy).

**Why this needs a reviewed design doc before implementation** (per the plan's own Task 04
wording and this repo's standing security-plan rule): the fix touches a trust boundary
(`verify_edit_ticket`'s pass/fail decision feeds agent edit authorization), the platform primitive
differs Windows-vs-POSIX (Part 1/2), and a naive fix risks the exact anti-pattern Part 2 names —
an unconditional platform-specific transform that closes the hole on one OS and reopens it on the
other. This is not a bounded refactor slice; it is new confinement logic on a security-relevant
path.

## Proposed contract (for review, not yet implemented)

1. **Directory pruning must check for reparse-point identity, not just membership in
   `_IGNORED_DEPENDENCY_DIRS`.** Before descending into any `dirnames` entry, resolve its identity
   via the platform primitive that actually detects a junction/reparse point (not
   `Path.is_symlink()` alone — per Part 5, use the toolchain probe's confirmed
   `is_symlink_dir`/`is_symlink_file`-shaped check, or `os.lstat().st_reparse_tag` on Windows /
   `os.path.islink()` on POSIX, whichever the pinned Rust/Python toolchain combination has already
   settled per the bounded-probe precedent in
   `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md` section 5). A directory whose
   identity is a symlink/junction/reparse point is **excluded from descent** by default — this is
   an edit-ticket population, not a general-purpose file walker, so fail-closed (exclude) is the
   correct default, not fail-open (follow and hope it's benign).
2. **File fingerprinting must use `follow_symlinks=False`-equivalent stat.** `item.stat()` becomes
   `item.lstat()` (POSIX) with the platform-appropriate Windows equivalent; a leaf that is itself a
   symlink is recorded as `unreadable_path`-shaped incomplete (or a new dedicated reason,
   `symlink_leaf_excluded`) rather than silently fingerprinting the link target's content.
3. **Preserve the "raw leaf identity" contract from Part 3.** A legitimately tracked symlink
   pointing *within* the repo (a common, benign pattern) should not be treated identically to one
   pointing *outside* — Part 3's rule is "never refused and never followed" for legitimate in-repo
   links. The design must resolve the link's target and classify it: target-within-root → follow
   and fingerprint normally; target-outside-root or unresolvable → exclude and report, never
   silently follow.
4. **Git-tracked identity as an alternative/complementary population source** (the plan's first
   listed remaining item): where the edit target's repo has a `.git` directory, prefer
   `git ls-files` (or equivalent porcelain) as the population source instead of a raw filesystem
   walk. Git's own tracked-file list already excludes untracked symlink escapes by construction
   (a symlink is tracked as its own blob type, not silently dereferenced), which sidesteps most of
   items 1-3 for the common case. The filesystem walk becomes the **fallback** for repos without
   `.git` (or where `git ls-files` fails), not the only path — meaning items 1-3 stay required, but
   exercised less often in practice.
5. **Full control-matrix re-run** (the plan's last remaining item): once implemented, re-run the
   existing bounded-budget control matrix (file-count limit, per-file-byte limit, aggregate-byte
   limit, unreadable-path) PLUS new symlink/junction control rows (in-root symlink target,
   out-of-root symlink target, junction-under-root on Windows, symlink leaf file) — all as a single
   parametrized suite so a future edit to one control can't silently regress another (the shape
   AGENTS.md's evidence laws call a mixed inclusive cap test).

## What this design doc does NOT decide

- The exact Windows API/flag for junction detection (deferred to implementation, which must cite
  the bounded-probe precedent rather than re-litigating the Part 5 "junctions are/aren't symlinks"
  question from scratch).
- Whether `git ls-files` becomes the *only* population source (item 4) or purely a fast-path ahead
  of the hardened filesystem walk — that's an implementation-time tradeoff between simplicity and
  fallback robustness, not a security decision this doc needs to pre-commit.
- Any change to `EditReadyTicketV1`'s on-disk schema — `population_status`'s existing shape
  (`status`/`reason`/`verified`) already has room for a new `reason` value; no version bump is
  anticipated but should be confirmed at implementation time.

## Review gate

Per the plan's Task 04 owner note ("existing S1/S5 identity design remains required") and this
repo's standing rule that security-plan changes need independent review before implementation:
this doc should go through a reviewed pass (senior-software-architect + security-trust-officer, or
an equivalent independent security review) before any implementation PR is opened against it. Not
self-approved by the same session that wrote it.
