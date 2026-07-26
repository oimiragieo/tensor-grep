# Plan — #307: where tg can genuinely LEAD on trust, not tie

Status: DRAFT (awaiting review)
Author: backlog-steward session, 2026-07-26
Goal: #292 / the CEO enterprise-readiness answer

## 1. The honest starting position

The reproducible cross-platform trust benchmark says tg **ties**:

| tool | admits | partial | silent |
| --- | --- | --- | --- |
| tg | 2 | 2 | 0 |
| ripgrep | 2 | 2 | 0 |
| GNU grep | 2 | 2 | 0 |
| git grep | 0 | 0 | 0 |

Identical on Windows and Linux. This falsified my own earlier "tg leads 5/5" headline. tg is not
worse than the incumbents on unreadable-path disclosure; it is simply not better.

## 2. The finding that changes the answer

**ripgrep has tg's `--json` defect and its maintainer has explicitly declined to fix it.**

- Its JSON Lines schema is `begin` / `end` / `match` / `context` plus a final `summary` whose
  `stats` is `{elapsed, searches, searches_with_match, bytes_searched, bytes_printed,
  matched_lines, matches}`. **There is no error field anywhere in it.**
- I/O errors go to stderr only. Completeness rides *solely* on exit code 2, which the docs describe
  as "true for both catastrophic errors ... and soft errors."
- Feature requests to classify errors in `--json` were declined as scope creep
  ([ripgrep#2861](https://github.com/BurntSushi/ripgrep/issues/2861)).

So for the consumer that matters most — an agent piping `--json` into `jq`, which never sees an exit
code — **ripgrep is structurally silent and cannot be fixed without its maintainer changing his
mind.** That is not a race tg can lose by being slower; it is a design position the incumbent has
publicly taken.

**Therefore #307 is not a separate project. It is the downstream effect of closing #276.**

## 3. Why the benchmark will register it automatically

`scripts/trust_benchmark.py:106-115` already scores exactly this:

```python
def _score(rc: int, _out: str, err: str, unreadable_name: str) -> tuple[int, str]:
    """Score one run. A non-zero exit OR a message naming the path is an admission."""
    named = unreadable_name and unreadable_name in err
    if rc not in (0, 1):
        return (ADMITS, f"exit {rc}" + ...)
```

The harness is a **ready-made bidirectional oracle** for #276: the control arm (today's native
`--json`) must score below `ADMITS`, and the treatment arm must score `ADMITS`. No new fixture is
needed, and the plan for #276 should name this as its primary verification harness rather than
inventing one.

## 4. The gap the current benchmark cannot see

Here is the catch, and it is the reason this plan exists rather than just a line in #276's.

The current scorer keys on **exit code and stderr**. Both tg and ripgrep already exit 2 and both
already write *something* to stderr, which is precisely why they tie at 2/2/0. **The benchmark as
written cannot distinguish "told me on stderr" from "told me in the payload I am actually
parsing."** That distinction is the entire enterprise thesis.

So closing #276 alone would move nothing on this scoreboard — the tie would persist while the real
difference grew. **That is oracle Form 7 in a new costume: a column that cannot discriminate the
thing we most want to measure.**

## 5. The change: score the CONSUMER's view, not the process's

Add a column that asks the question an agent actually asks:

> Parsing **only stdout** — never the exit code, never stderr — can I tell that this result is
> incomplete?

Proposed scoring, deliberately parallel to the existing one:

| Score | Meaning |
| --- | --- |
| `ADMITS` | stdout alone carries a machine-readable incompleteness marker |
| `PARTIAL` | stdout carries something a human could interpret, but no structured field |
| `SILENT` | stdout is indistinguishable from a complete result |

Expected today: **tg SILENT, ripgrep SILENT, GNU grep SILENT, git grep SILENT** — a clean 0/0/4.

That is a legitimate tied-at-floor column *only because it is measuring a real gap nobody has closed
yet*, and it becomes discriminating the moment #276 lands. **The Form-7 rule is not "never ship a
tied column" — it is "never ship a column that cannot ever separate."** This one separates by
construction as soon as the producer changes, and the plan must state the expected before/after as a
precondition so the next reader can check the instrument.

### The discipline that must ride with it

Per the Form-7 lesson (#302, the vanished-file column): **if this column is still 0/0/4 after #276
lands, the fix did not reach the consumer** — that is a finding, not a scoring quirk. Write that
expectation into the harness comment so it cannot be quietly rationalized later.

## 6. What NOT to do

| Do not | Why |
| --- | --- |
| Claim a lead before #276 lands | The current tie is real and I already published one false "5/5 leads" headline; a second would cost more than the first |
| Add columns to inflate the count | Form 7 — a column that cannot discriminate is worse than none, because it looks like data |
| Score speed here | Settled: startup is at parity, GPU is dead, beating rg on cold search is a closed negative. This benchmark measures honesty; keep it that way |
| Treat "names the path on stderr" as equivalent | That is exactly the conflation §4 identifies. A path named on stderr is invisible to a `jq` pipeline |

## 7. Verification

- **Bidirectional on the new column**: a synthetic tool that prints a marker into stdout must score
  `ADMITS`; one that prints the identical text to stderr must score `SILENT`. If both score the
  same, the column is measuring the old thing again.
- **Control arm stated as a precondition** in the harness: "before #276, every tool including tg
  scores SILENT here." A reader who sees anything else knows the instrument changed, not the world.
- **Cross-platform**, as the existing arms are — Windows and Linux must agree, and disagreement is
  itself a finding.

## 8. Open questions

1. Should the new column be added **now** (tied at floor, honest, ready to discriminate) or **with**
   #276 (never ships a tied column at all)? Shipping it now is the more falsifiable choice: it
   commits the prediction *before* the result, which is exactly what makes the eventual lead
   credible rather than post-hoc.
2. Does `--ndjson` need its own column, or does it share `--json`'s verdict?
3. Should the benchmark ever publish? That is CEO-gated (#72) and out of scope here.
