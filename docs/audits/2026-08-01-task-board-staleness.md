# Why `docs/TASK_BOARD.md` keeps going stale — root cause, one mechanism, one retirement

**The board has gone stale four times in the same way.** Its own header records three; the
2026-08-01 backlog campaign found the fourth by verifying every open item against the code:
**nine of them were already fixed, refuted, or deliberate-by-design.** Only seven were genuinely
open, and just one carried user-facing harm.

This document exists so a fifth session does not simply correct it a fifth time.

## What the board already decided, and what it missed

The header considers two CI gates and rejects both. **Both rejections are correct and stand:**

1. *Assert the IN FLIGHT table matches `gh pr list`.* Needs network and a token inside the test
   run. An offline or rate-limited run reds the build for a reason unrelated to the repo — which
   teaches people to reach for `--no-verify` and discredits every other gate here.
2. *Assert the `post-vX.Y.Z` stamp equals `pyproject.toml`'s version.* Zero network, perfectly
   deterministic — and it would fire after **every** release, several times a day, forcing a board
   edit into every unrelated PR. An over-eager rule is worse than no rule.

**Neither considered a TOLERANCE.** Rejection 2 argues against the *strict equality* form. It does
not argue against letting the stamp lag a few releases and failing only on genuine neglect. That gap
is where the one buildable mechanism lives.

## Shipped: a tolerance gate

`tests/unit/test_task_board_freshness.py` fails when the board's reconcile stamp is more than
**5 releases** behind `pyproject.toml`.

- **Zero network.** Both numbers are read from files in the repo.
- **Deterministic.** No clock, no ordering, no environment.
- **Not over-eager.** A 1–2 release lag passes untouched.

The threshold is sized from the board's **recorded incidents**, not taste: it once read
`post-v1.101.9` while the world had "moved 13 releases on", and later sat 8 behind. Both are pinned
as parameterized cases, alongside two must-NOT-fire cases, so a future edit to the threshold has to
confront exactly what it breaks.

**Bidirectional proof.** Rewinding the live stamp to the real 13-release incident fails with
`assert 19 <= 5` and the full reconcile guidance; restored, all 7 pass and the file is byte-identical.
The mutation was asserted to have applied before the red run — an inert mutation otherwise reads as a
passing control.

## Retired: content-level staleness is not mechanically detectable here

**This is the more important half of the finding.** The gate proves the stamp is RECENT. It cannot
prove the CONTENT is correct — an item can say OPEN about work that shipped months ago while the
stamp is perfectly current. That is exactly the failure that occurred nine times.

The obvious candidate was to extend `tests/unit/test_skill_library_drift.py` (which pins citations in
the skill library) to the board. **Measured, and it fails the acceptance test:**

```
open items on the board          24
open items citing a file/symbol   3
```

A citation-anchored checker would cover **12.5%** of the board, and even for those three it would
only prove the citation *resolves* — its own documented limitation — not that the described defect
still exists. The live example is `--quiet silently dropped by both internal rg-passthrough
branches`, still listed OPEN with a perfectly resolvable `main.py:7937-7943` citation, months after
commit `cfc3264` fixed it. No resolution check can see that; it requires reading the code and
understanding the claim.

**So it is retired with the reason recorded**, per board rule 4 — a documented retirement is worth as
much as a fix, because it stops the next session re-deriving it. Do not build a citation gate for the
board and do not re-litigate rejections 1 and 2.

## The actual fix is a routine, not a test

The board's own header already named it, and the fix has never been the problem:

> The reconcile step belongs in the merge routine — the same turn the PR merges, before the next
> item is picked up — not in a cleanup pass that only happens when the board embarrasses someone.

Three warnings were ignored because a warning is not a mechanism. What changed here is narrow and
worth stating plainly:

- The **tolerance gate** makes prolonged neglect impossible to ignore — it turns a silent drift into
  a red build, but only once the drift is real.
- Content correctness stays a **human/agent routine**, now with its impossibility documented rather
  than implied, so nobody mistakes a green gate for a reconciled board.

**Harden a rule when a violation is mechanically detectable without interpretation AND a false
positive would be rare.** For the stamp, both now hold. For item state, neither does.
