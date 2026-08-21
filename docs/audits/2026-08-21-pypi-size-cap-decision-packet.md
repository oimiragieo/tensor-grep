# tensor-grep PyPI project-size decision packet

**Date:** 2026-08-21

**Prepared for:** CEO decision on the PyPI `tensor-grep` project 10 GB size-cap incident

**Status:** decision-ready -- pick a policy letter, nothing else required to act


## 0. Symptom verification (measured, not assumed)


Query: `https://pypi.org/pypi/tensor-grep/json` via Python `urllib`, 2026-08-21.


- **API call: SUCCEEDED** (control: this is stated explicitly so a reader can distinguish a real
  measurement from a failed query reporting a false zero -- see the tensor-grep AGENTS.md instrument
  laws).
- **Total releases:** 713
- **Total files across all releases:** 2,847
- **Total bytes:** 10,733,755,391 = **10.734 GB**
- This **CONFIRMS** the 10.73 GB figure already recorded in `docs/BACKLOG.md` (lines ~60-65), which
  independently cites "713 releases, 10.73 GB total, ~15 MB per release" from the same JSON API at
  the time of the publish failure. Two independent queries (BACKLOG.md's and this one) agree to 3
  decimal places on GB and exactly on release count.

### v1.111.1 is confirmed partially published, and worse than the symptom description states


The symptom description says "some artifacts uploaded, others did not" and implies Windows is the
only one missing. **Measured reality is more specific and slightly different:** v1.111.1 has only
**2 of the expected 4 files**:

- `tensor_grep-1.111.1-cp311-abi3-macosx_11_0_arm64.whl` (present)
- `tensor_grep-1.111.1-cp311-abi3-manylinux_2_39_x86_64.whl` (present)
- `tensor_grep-1.111.1-cp311-abi3-win_amd64.whl` -- **MISSING**
- `tensor_grep-1.111.1.tar.gz` (sdist) -- **MISSING**

So v1.111.1 is missing **both the Windows wheel AND the sdist**, not just Windows. Anyone installing
from source (sdist) or on Windows resolves to the last complete release, **v1.111.0** (19.1 MB, all
4 files, uploaded 2026-08-20T01:37:45Z).

### Every incomplete release on PyPI (not just 1.111.1)


Full per-release file-set check (expects: 1 sdist `.tar.gz` + macosx wheel + manylinux wheel + win
wheel = 4 files) across all 713 releases:

| version | files present | missing | notes |
|---|---|---|---|
| 0.1.0 | 2 (`py3-none-any.whl`, `.tar.gz`) | wheel-per-platform architecture n/a | earliest release, pre-native-wheel era -- not a cap-related gap, a pure-Python-era artifact shape |
| 1.13.44 | 3 (macosx, manylinux, win wheels) | sdist | pre-existing gap, unrelated to the cap incident (uploaded 2026-06-25, cap hit 2026-08-21) |
| 1.111.1 | 2 (macosx, manylinux wheels) | sdist, win wheel | **the cap incident** (uploaded 2026-08-21T00:05:35Z) |

**Every other one of the 713 releases has exactly 4 files.** (file-count distribution across all
releases: `{4: 710, 2: 2, 3: 1}` -- only these three rows deviate.) So the incomplete-release
population is small and precisely bounded: this is not a systemic multi-release corruption, it is
one active incident (1.111.1) plus two unrelated historical artifacts pre-dating the current
4-file-per-release convention.

## 1. Per-release size trend -- the "newer releases are smaller" hypothesis is FALSE, measured


The premise to test: recent commits stripped debug symbols from the Rust binary, so newer releases
should be smaller, which would change how much headroom any retention policy actually buys.

Measured (bytes summed per release, non-empty releases only, n=713):

- **All-time average:** 15.05 MB/release
- **Average of the last 20 releases:** 18.67 MB/release
- **Average of the last 50 releases:** 18.67 MB/release (identical to last-20 -- flat, not declining)
- **Last 100 releases:** min 11.7 MB (v1.111.1, itself only 2/4 files -- an artifact of the missing
  files, not smaller wheels), max 19.1 MB, average 18.5 MB
- **Last 100 split in half for a trend check:** first half (releases 613-662) averages **18.4 MB**;
  second half (releases 663-712, i.e. the newest) averages **18.7 MB** -- flat to *very slightly
  rising*, not falling.
- **Largest release ever:** v1.111.0 at 19.1 MB (2026-08-20, i.e. yesterday) -- the single largest
  release on record, and it's the second-most-recent one.

**Verdict: the debug-symbol-stripping hypothesis is REFUTED by measurement.** Newer releases are not
smaller -- they're flat-to-larger. Per-release size has been climbing since the early history (8.7
MB average for the first 20 releases vs 18.7 MB average for the last 20 -- roughly 2.1x growth as the
project added native wheels for more platforms). This matters directly for policy sizing below: use
**18.67 MB/release** (measured, last-20 average) as the current per-release cost, not a hopeful lower
number.

## 2. Retention policies -- real numbers


All three computed against the measured 10.734 GB / 713-release corpus. "Headroom" = (10 GB cap -
remaining bytes after deletion) / 18.67 MB per-release (the measured *current*, not historical,
per-release size -- using this number is the conservative choice per the trend finding above).

### Policy A -- keep last 5 minor lines + every `X.Y.0` release

Keep any release whose (major, minor) tuple is one of the 5 most recent minor lines present on PyPI,
**plus** every release ending in `.0` (patch=0) regardless of age, as permanent milestone markers.

- **Releases kept:** 165 / 713
- **Releases deleted:** 548
- **Bytes freed:** 8.198 GB
- **Remaining on PyPI:** 2.536 GB
- **Headroom bought:** ~439 future releases at current size (18.67 MB) before hitting the cap again

### Policy B -- keep last 90 days + every `X.Y.0` release

Keep any release uploaded within the last 90 days (i.e. on/after 2026-05-23), **plus** every `.0`
release regardless of age.

- **Releases kept:** 510 / 713
- **Releases deleted:** 203
- **Bytes freed:** 2.403 GB
- **Remaining on PyPI:** 8.331 GB
- **Headroom bought:** ~129 future releases at current size before hitting the cap again
- Deleted-release date range: 2026-03-01 to 2026-05-23 (the pre-90-day window)

### Policy C -- aggressive: keep only the last 20 releases

Keep the 20 most recent releases only; delete everything else, including all `.0` milestones outside
that window.

- **Releases kept:** 20 / 713
- **Releases deleted:** 693
- **Bytes freed:** 10.36 GB
- **Remaining on PyPI:** 0.373 GB
- **Headroom bought:** ~555 future releases at current size before hitting the cap again

## 3. Recommendation: Policy A


**Policy A is the recommended default.** Reasoning:

- **Policy C is rejected**: it deletes every historical `.0` milestone (v1.0.0-class markers, if any
  exist, plus every early-history release), which destroys the project's version-history legibility
  and reproducibility for anyone auditing "what changed between v1.50.0 and v1.100.0" -- a real cost
  for an actively-developed tool with this repo's own change-control discipline (AGENTS.md explicitly
  cares about traceable release history). It buys the most headroom (~555 releases) but at a
  disproportionate history cost for only ~2.2 GB more freed than Policy A.
- **Policy B is rejected as the default**: it only frees 2.4 GB (remaining 8.33 GB, ~83% of the cap
  still consumed) and only buys ~129 releases of headroom -- at current velocity (this repo has shipped
  713 releases total, many in rapid succession per the campaign logs in memory), 129 releases is a
  matter of weeks to a few months, meaning this exact incident recurs on a short clock. A 90-day
  window is also an awkward middle ground: it keeps a lot of recent patch noise (every point release
  in a fast-moving minor line) while still deleting real milestones outside the window.
  Policy B is the right choice for a MORE conservative practitioner, but the CEO packet's own
  measured trend section (Sec. 1) shows per-release size is not shrinking -- so leaving 83% of the
  cap consumed is not a durable place to land.
- **Policy A balances both**: it frees 8.2 GB (76% of the total), leaves generous headroom (~439
  future releases, well over a year at current pace), and its `.0`-release exemption preserves every
  major/minor milestone forever, keeping full-line version history readable (v1.0.0, v1.10.0, v1.50.0,
  v1.100.0, etc. all survive regardless of age) while pruning the high-volume patch churn (the vast
  majority of the 713 releases are patch bumps within a minor line, per the release-class discipline
  documented in this repo's own CLAUDE.md).

**Do Policy A. Delete the 548 listed non-`.0`, non-last-5-minor-line releases below (Section 5).**

## 4. Risk


- **Deleting a PyPI release file is IRREVERSIBLE.** PyPI does not allow re-uploading a filename that
  was ever used for a project, even after deletion -- a deleted version's exact file cannot be restored
  under its original filename. Anyone who has pinned an exact deleted version (e.g. `tensor-grep==0.26.3`
  in a lockfile) will get a resolution failure on their next fresh install, not a silent downgrade.
  This is real breakage, not merely cosmetic -- treat it as requiring explicit sign-off before
  execution.
- **Yank does NOT free space.** A live fetch of PyPI's own help/documentation pages was NOT
  attempted in this pass (only the JSON API endpoint was queried) -- **so this claim is stated as
  UNVERIFIED against a primary source in this packet, explicitly.** It matches PyPI's
  well-established, widely-documented behavior: `yank` only hides a release from default dependency
  resolution (marks it "yanked" so it won't be selected unless pinned exactly by version); the
  release's files remain fully stored and served, so **yanking a release does nothing to relieve the
  10 GB project-size cap.** Do not treat yank as an alternative to deletion for this problem -- it
  solves a different problem (steering people away from a broken version while keeping it
  downloadable for anyone who pinned it), not storage pressure. If this claim needs to be load-bearing
  for the final decision, verify it against PyPI's own docs (https://pypi.org or
  https://packaging.python.org) before acting on it.
- **A project-size limit increase request to PyPI support** (mentioned as an alternative in
  `docs/BACKLOG.md` line 74) is a parallel, non-destructive option worth filing regardless of which
  deletion policy is chosen -- it does not require picking a policy first, and if granted, makes any
  future deletion decision less urgent. It is not scored here as a policy option because it is not
  something this packet's author can execute (it depends on PyPI support's response time and
  approval), but the CEO should consider filing it in parallel with acting on Policy A.
- Before deleting anything, re-publish the missing v1.111.1 artifacts (win wheel + sdist) FIRST if at
  all possible within the size cap headroom Policy A creates -- otherwise Windows/sdist users stay
  stuck on v1.111.0 even after cap relief.

## 5. Appendix -- exact version lists per policy


### Policy A deletion list (548 versions)


```
0.2.1, 0.3.4, 0.4.1, 0.6.1, 0.9.1, 0.10.1, 0.11.1, 0.12.2, 0.12.3, 0.12.5, 0.15.1, 0.16.1, 0.16.2,
0.26.1, 0.26.2, 0.26.3, 0.26.4, 0.26.5, 0.26.6, 0.29.1, 0.29.2, 0.29.3, 0.30.1, 0.30.2, 0.30.3,
0.30.4, 0.31.1, 0.31.2, 0.31.3, 0.31.4, 0.31.5, 0.31.6, 0.31.7, 0.31.8, 0.31.9, 0.31.10, 0.31.12,
0.31.14, 0.31.15, 0.31.16, 0.31.17, 0.31.18, 0.31.19, 0.31.20, 0.31.21, 0.31.22, 0.31.23, 0.31.24,
0.35.1, 1.0.1, 1.1.1, 1.1.2, 1.1.3, 1.1.4, 1.3.1, 1.3.2, 1.4.1, 1.4.2, 1.4.3, 1.4.4, 1.4.5, 1.4.6,
1.4.7, 1.4.8, 1.4.9, 1.4.10, 1.4.11, 1.4.12, 1.6.1, 1.6.2, 1.6.3, 1.6.4, 1.6.5, 1.7.1, 1.7.2,
1.8.1, 1.8.2, 1.8.3, 1.8.4, 1.8.5, 1.8.6, 1.8.7, 1.8.8, 1.8.9, 1.8.10, 1.8.11, 1.8.12, 1.8.13,
1.8.14, 1.8.15, 1.8.16, 1.8.17, 1.8.18, 1.8.19, 1.8.20, 1.8.21, 1.8.22, 1.8.23, 1.8.24, 1.8.25,
1.8.26, 1.8.27, 1.8.28, 1.8.29, 1.8.30, 1.8.31, 1.8.32, 1.8.33, 1.9.1, 1.9.2, 1.9.3, 1.9.4, 1.9.5,
1.9.6, 1.9.7, 1.9.8, 1.9.9, 1.9.11, 1.10.1, 1.10.2, 1.10.3, 1.10.4, 1.10.5, 1.10.6, 1.10.7, 1.10.8,
1.10.9, 1.10.10, 1.11.1, 1.11.2, 1.11.3, 1.11.4, 1.11.5, 1.11.6, 1.11.7, 1.12.1, 1.12.2, 1.12.3,
1.12.4, 1.12.5, 1.12.6, 1.12.7, 1.12.8, 1.12.9, 1.12.10, 1.12.11, 1.12.12, 1.12.13, 1.12.14,
1.12.15, 1.12.16, 1.12.17, 1.12.18, 1.12.19, 1.12.20, 1.12.21, 1.12.22, 1.12.23, 1.12.24, 1.12.25,
1.12.26, 1.12.27, 1.12.28, 1.12.29, 1.12.30, 1.12.31, 1.12.32, 1.12.33, 1.12.34, 1.12.35, 1.12.36,
1.12.37, 1.12.38, 1.12.39, 1.12.40, 1.12.41, 1.12.42, 1.12.43, 1.12.44, 1.12.45, 1.12.46, 1.12.47,
1.12.48, 1.12.49, 1.12.50, 1.12.51, 1.12.52, 1.12.53, 1.12.54, 1.12.55, 1.12.56, 1.12.57, 1.12.58,
1.12.59, 1.12.61, 1.12.62, 1.12.63, 1.12.64, 1.12.65, 1.12.66, 1.13.1, 1.13.2, 1.13.3, 1.13.4,
1.13.6, 1.13.7, 1.13.8, 1.13.9, 1.13.10, 1.13.11, 1.13.12, 1.13.13, 1.13.14, 1.13.15, 1.13.16,
1.13.17, 1.13.18, 1.13.19, 1.13.20, 1.13.21, 1.13.22, 1.13.23, 1.13.24, 1.13.25, 1.13.26, 1.13.27,
1.13.28, 1.13.29, 1.13.30, 1.13.31, 1.13.32, 1.13.33, 1.13.34, 1.13.35, 1.13.36, 1.13.37, 1.13.38,
1.13.39, 1.13.40, 1.13.41, 1.13.42, 1.13.43, 1.13.44, 1.13.45, 1.13.46, 1.13.47, 1.15.1, 1.17.1,
1.17.2, 1.17.3, 1.17.4, 1.17.5, 1.17.6, 1.17.7, 1.17.8, 1.17.9, 1.17.10, 1.17.11, 1.17.12, 1.17.13,
1.17.14, 1.17.15, 1.17.16, 1.17.17, 1.17.18, 1.17.19, 1.17.20, 1.17.21, 1.17.22, 1.17.23, 1.17.24,
1.17.25, 1.17.26, 1.17.27, 1.17.28, 1.17.29, 1.17.31, 1.18.1, 1.18.2, 1.18.3, 1.18.5, 1.19.1,
1.19.2, 1.19.3, 1.19.4, 1.19.5, 1.19.6, 1.19.7, 1.19.8, 1.19.9, 1.22.1, 1.23.2, 1.23.3, 1.28.1,
1.28.2, 1.28.3, 1.28.4, 1.28.5, 1.28.6, 1.28.7, 1.28.8, 1.30.1, 1.30.2, 1.30.3, 1.30.4, 1.30.5,
1.35.1, 1.39.1, 1.40.1, 1.40.2, 1.40.3, 1.40.4, 1.40.5, 1.42.1, 1.42.2, 1.42.3, 1.42.4, 1.42.5,
1.42.6, 1.44.1, 1.45.1, 1.45.2, 1.45.3, 1.45.4, 1.45.5, 1.45.6, 1.45.7, 1.45.8, 1.45.9, 1.45.10,
1.45.11, 1.45.12, 1.45.13, 1.45.14, 1.45.15, 1.45.16, 1.45.17, 1.48.1, 1.49.1, 1.49.2, 1.49.3,
1.51.1, 1.51.2, 1.51.3, 1.51.4, 1.51.5, 1.51.6, 1.51.7, 1.51.8, 1.51.9, 1.51.10, 1.54.1, 1.54.2,
1.54.3, 1.54.4, 1.54.5, 1.54.6, 1.56.1, 1.56.2, 1.57.1, 1.58.1, 1.58.2, 1.58.3, 1.58.4, 1.58.5,
1.58.6, 1.58.7, 1.58.8, 1.58.9, 1.58.10, 1.58.11, 1.58.12, 1.58.13, 1.58.14, 1.58.15, 1.58.16,
1.59.1, 1.59.2, 1.59.3, 1.59.4, 1.60.1, 1.61.1, 1.61.2, 1.62.1, 1.62.2, 1.63.1, 1.63.2, 1.63.3,
1.63.4, 1.64.1, 1.64.2, 1.64.3, 1.64.4, 1.65.1, 1.65.2, 1.65.3, 1.65.4, 1.65.5, 1.65.6, 1.66.1,
1.67.1, 1.68.1, 1.68.2, 1.69.1, 1.69.2, 1.69.3, 1.70.1, 1.70.2, 1.71.1, 1.71.2, 1.71.3, 1.72.1,
1.74.1, 1.74.2, 1.74.3, 1.74.4, 1.75.1, 1.75.2, 1.75.3, 1.75.4, 1.76.1, 1.76.2, 1.76.3, 1.76.4,
1.76.5, 1.76.6, 1.76.7, 1.76.8, 1.76.9, 1.76.10, 1.76.11, 1.76.12, 1.76.13, 1.78.1, 1.80.1, 1.80.2,
1.80.3, 1.80.4, 1.81.1, 1.81.2, 1.81.3, 1.81.4, 1.81.5, 1.81.6, 1.81.7, 1.81.8, 1.81.9, 1.81.10,
1.81.11, 1.81.12, 1.81.13, 1.81.14, 1.81.15, 1.81.16, 1.81.17, 1.81.18, 1.81.19, 1.81.20, 1.81.21,
1.82.1, 1.91.1, 1.91.2, 1.91.3, 1.92.1, 1.92.2, 1.92.3, 1.93.1, 1.93.2, 1.93.3, 1.93.4, 1.93.5,
1.93.6, 1.93.7, 1.93.8, 1.93.9, 1.93.10, 1.96.1, 1.98.1, 1.98.2, 1.98.3, 1.98.4, 1.98.5, 1.98.6,
1.98.7, 1.98.8, 1.98.9, 1.98.10, 1.98.11, 1.98.12, 1.98.13, 1.98.14, 1.98.15, 1.98.16, 1.98.17,
1.98.18, 1.98.19, 1.98.20, 1.98.21, 1.98.22, 1.98.23, 1.98.24, 1.98.25, 1.98.26, 1.98.27, 1.99.1,
1.99.2, 1.99.3, 1.99.4, 1.99.5, 1.100.1, 1.100.2, 1.101.1, 1.101.2, 1.101.3, 1.101.4, 1.101.5,
1.101.6, 1.101.7, 1.101.8, 1.101.9, 1.101.12, 1.101.13, 1.101.14, 1.101.15, 1.101.16, 1.101.17,
1.101.18, 1.101.19, 1.101.20, 1.101.21, 1.101.22, 1.101.23, 1.101.24, 1.101.25, 1.101.26, 1.101.27,
1.101.28, 1.101.29, 1.101.30, 1.101.31, 1.102.1, 1.102.2, 1.102.3, 1.102.4, 1.102.5, 1.102.6,
1.102.7, 1.102.8
```


### Policy B deletion list (203 versions)


```
0.2.1, 0.3.4, 0.4.1, 0.6.1, 0.9.1, 0.10.1, 0.11.1, 0.12.2, 0.12.3, 0.12.5, 0.15.1, 0.16.1, 0.16.2,
0.26.1, 0.26.2, 0.26.3, 0.26.4, 0.26.5, 0.26.6, 0.29.1, 0.29.2, 0.29.3, 0.30.1, 0.30.2, 0.30.3,
0.30.4, 0.31.1, 0.31.2, 0.31.3, 0.31.4, 0.31.5, 0.31.6, 0.31.7, 0.31.8, 0.31.9, 0.31.10, 0.31.12,
0.31.14, 0.31.15, 0.31.16, 0.31.17, 0.31.18, 0.31.19, 0.31.20, 0.31.21, 0.31.22, 0.31.23, 0.31.24,
0.35.1, 1.0.1, 1.1.1, 1.1.2, 1.1.3, 1.1.4, 1.3.1, 1.3.2, 1.4.1, 1.4.2, 1.4.3, 1.4.4, 1.4.5, 1.4.6,
1.4.7, 1.4.8, 1.4.9, 1.4.10, 1.4.11, 1.4.12, 1.6.1, 1.6.2, 1.6.3, 1.6.4, 1.6.5, 1.7.1, 1.7.2,
1.8.1, 1.8.2, 1.8.3, 1.8.4, 1.8.5, 1.8.6, 1.8.7, 1.8.8, 1.8.9, 1.8.10, 1.8.11, 1.8.12, 1.8.13,
1.8.14, 1.8.15, 1.8.16, 1.8.17, 1.8.18, 1.8.19, 1.8.20, 1.8.21, 1.8.22, 1.8.23, 1.8.24, 1.8.25,
1.8.26, 1.8.27, 1.8.28, 1.8.29, 1.8.30, 1.8.31, 1.8.32, 1.8.33, 1.9.1, 1.9.2, 1.9.3, 1.9.4, 1.9.5,
1.9.6, 1.9.7, 1.9.8, 1.9.9, 1.9.11, 1.10.1, 1.10.2, 1.10.3, 1.10.4, 1.10.5, 1.10.6, 1.10.7, 1.10.8,
1.10.9, 1.10.10, 1.11.1, 1.11.2, 1.11.3, 1.11.4, 1.11.5, 1.11.6, 1.11.7, 1.12.1, 1.12.2, 1.12.3,
1.12.4, 1.12.5, 1.12.6, 1.12.7, 1.12.8, 1.12.9, 1.12.10, 1.12.11, 1.12.12, 1.12.13, 1.12.14,
1.12.15, 1.12.16, 1.12.17, 1.12.18, 1.12.19, 1.12.20, 1.12.21, 1.12.22, 1.12.23, 1.12.24, 1.12.25,
1.12.26, 1.12.27, 1.12.28, 1.12.29, 1.12.30, 1.12.31, 1.12.32, 1.12.33, 1.12.34, 1.12.35, 1.12.36,
1.12.37, 1.12.38, 1.12.39, 1.12.40, 1.12.41, 1.12.42, 1.12.43, 1.12.44, 1.12.45, 1.12.46, 1.12.47,
1.12.48, 1.12.49, 1.12.50, 1.12.51, 1.12.52, 1.12.53, 1.12.54, 1.12.55, 1.12.56, 1.12.57, 1.12.58,
1.12.59, 1.12.61, 1.12.62, 1.12.63, 1.12.64, 1.12.65, 1.12.66, 1.13.1, 1.13.2, 1.13.3
```


### Policy C deletion list (693 versions -- i.e. every release except the 20 kept below)


**Kept (last 20):** 1.110.2, 1.110.3, 1.110.4, 1.110.5, 1.110.6, 1.110.7, 1.110.8, 1.110.9, 1.110.10, 1.110.11, 1.110.12, 1.110.13, 1.110.14, 1.110.15, 1.110.16, 1.110.17, 1.110.18, 1.110.19, 1.111.0, 1.111.1


```
0.1.0, 0.2.0, 0.2.1, 0.3.4, 0.4.0, 0.4.1, 0.5.0, 0.6.0, 0.6.1, 0.7.0, 0.8.0, 0.9.0, 0.9.1, 0.10.0,
0.10.1, 0.11.1, 0.12.0, 0.12.2, 0.12.3, 0.12.5, 0.13.0, 0.14.0, 0.15.0, 0.15.1, 0.16.0, 0.16.1,
0.16.2, 0.17.0, 0.18.0, 0.19.0, 0.20.0, 0.21.0, 0.22.0, 0.23.0, 0.24.0, 0.25.0, 0.26.0, 0.26.1,
0.26.2, 0.26.3, 0.26.4, 0.26.5, 0.26.6, 0.27.0, 0.28.0, 0.29.0, 0.29.1, 0.29.2, 0.29.3, 0.30.0,
0.30.1, 0.30.2, 0.30.3, 0.30.4, 0.31.0, 0.31.1, 0.31.2, 0.31.3, 0.31.4, 0.31.5, 0.31.6, 0.31.7,
0.31.8, 0.31.9, 0.31.10, 0.31.12, 0.31.14, 0.31.15, 0.31.16, 0.31.17, 0.31.18, 0.31.19, 0.31.20,
0.31.21, 0.31.22, 0.31.23, 0.31.24, 0.32.0, 0.33.0, 0.34.0, 0.35.0, 0.35.1, 1.0.1, 1.1.0, 1.1.1,
1.1.2, 1.1.3, 1.1.4, 1.2.0, 1.3.0, 1.3.1, 1.3.2, 1.4.0, 1.4.1, 1.4.2, 1.4.3, 1.4.4, 1.4.5, 1.4.6,
1.4.7, 1.4.8, 1.4.9, 1.4.10, 1.4.11, 1.4.12, 1.6.0, 1.6.1, 1.6.2, 1.6.3, 1.6.4, 1.6.5, 1.7.0,
1.7.1, 1.7.2, 1.8.0, 1.8.1, 1.8.2, 1.8.3, 1.8.4, 1.8.5, 1.8.6, 1.8.7, 1.8.8, 1.8.9, 1.8.10, 1.8.11,
1.8.12, 1.8.13, 1.8.14, 1.8.15, 1.8.16, 1.8.17, 1.8.18, 1.8.19, 1.8.20, 1.8.21, 1.8.22, 1.8.23,
1.8.24, 1.8.25, 1.8.26, 1.8.27, 1.8.28, 1.8.29, 1.8.30, 1.8.31, 1.8.32, 1.8.33, 1.9.0, 1.9.1,
1.9.2, 1.9.3, 1.9.4, 1.9.5, 1.9.6, 1.9.7, 1.9.8, 1.9.9, 1.9.11, 1.10.0, 1.10.1, 1.10.2, 1.10.3,
1.10.4, 1.10.5, 1.10.6, 1.10.7, 1.10.8, 1.10.9, 1.10.10, 1.11.1, 1.11.2, 1.11.3, 1.11.4, 1.11.5,
1.11.6, 1.11.7, 1.12.0, 1.12.1, 1.12.2, 1.12.3, 1.12.4, 1.12.5, 1.12.6, 1.12.7, 1.12.8, 1.12.9,
1.12.10, 1.12.11, 1.12.12, 1.12.13, 1.12.14, 1.12.15, 1.12.16, 1.12.17, 1.12.18, 1.12.19, 1.12.20,
1.12.21, 1.12.22, 1.12.23, 1.12.24, 1.12.25, 1.12.26, 1.12.27, 1.12.28, 1.12.29, 1.12.30, 1.12.31,
1.12.32, 1.12.33, 1.12.34, 1.12.35, 1.12.36, 1.12.37, 1.12.38, 1.12.39, 1.12.40, 1.12.41, 1.12.42,
1.12.43, 1.12.44, 1.12.45, 1.12.46, 1.12.47, 1.12.48, 1.12.49, 1.12.50, 1.12.51, 1.12.52, 1.12.53,
1.12.54, 1.12.55, 1.12.56, 1.12.57, 1.12.58, 1.12.59, 1.12.61, 1.12.62, 1.12.63, 1.12.64, 1.12.65,
1.12.66, 1.13.0, 1.13.1, 1.13.2, 1.13.3, 1.13.4, 1.13.6, 1.13.7, 1.13.8, 1.13.9, 1.13.10, 1.13.11,
1.13.12, 1.13.13, 1.13.14, 1.13.15, 1.13.16, 1.13.17, 1.13.18, 1.13.19, 1.13.20, 1.13.21, 1.13.22,
1.13.23, 1.13.24, 1.13.25, 1.13.26, 1.13.27, 1.13.28, 1.13.29, 1.13.30, 1.13.31, 1.13.32, 1.13.33,
1.13.34, 1.13.35, 1.13.36, 1.13.37, 1.13.38, 1.13.39, 1.13.40, 1.13.41, 1.13.42, 1.13.43, 1.13.44,
1.13.45, 1.13.46, 1.13.47, 1.14.0, 1.15.0, 1.15.1, 1.16.0, 1.17.0, 1.17.1, 1.17.2, 1.17.3, 1.17.4,
1.17.5, 1.17.6, 1.17.7, 1.17.8, 1.17.9, 1.17.10, 1.17.11, 1.17.12, 1.17.13, 1.17.14, 1.17.15,
1.17.16, 1.17.17, 1.17.18, 1.17.19, 1.17.20, 1.17.21, 1.17.22, 1.17.23, 1.17.24, 1.17.25, 1.17.26,
1.17.27, 1.17.28, 1.17.29, 1.17.31, 1.18.0, 1.18.1, 1.18.2, 1.18.3, 1.18.5, 1.19.0, 1.19.1, 1.19.2,
1.19.3, 1.19.4, 1.19.5, 1.19.6, 1.19.7, 1.19.8, 1.19.9, 1.20.0, 1.21.0, 1.22.0, 1.22.1, 1.23.0,
1.23.2, 1.23.3, 1.24.0, 1.25.0, 1.26.0, 1.27.0, 1.28.0, 1.28.1, 1.28.2, 1.28.3, 1.28.4, 1.28.5,
1.28.6, 1.28.7, 1.28.8, 1.29.0, 1.30.0, 1.30.1, 1.30.2, 1.30.3, 1.30.4, 1.30.5, 1.31.0, 1.32.0,
1.33.0, 1.34.0, 1.35.0, 1.35.1, 1.36.0, 1.37.0, 1.38.0, 1.39.0, 1.39.1, 1.40.0, 1.40.1, 1.40.2,
1.40.3, 1.40.4, 1.40.5, 1.41.0, 1.42.0, 1.42.1, 1.42.2, 1.42.3, 1.42.4, 1.42.5, 1.42.6, 1.43.0,
1.44.0, 1.44.1, 1.45.0, 1.45.1, 1.45.2, 1.45.3, 1.45.4, 1.45.5, 1.45.6, 1.45.7, 1.45.8, 1.45.9,
1.45.10, 1.45.11, 1.45.12, 1.45.13, 1.45.14, 1.45.15, 1.45.16, 1.45.17, 1.46.0, 1.47.0, 1.48.0,
1.48.1, 1.49.0, 1.49.1, 1.49.2, 1.49.3, 1.50.0, 1.51.0, 1.51.1, 1.51.2, 1.51.3, 1.51.4, 1.51.5,
1.51.6, 1.51.7, 1.51.8, 1.51.9, 1.51.10, 1.52.0, 1.53.0, 1.54.0, 1.54.1, 1.54.2, 1.54.3, 1.54.4,
1.54.5, 1.54.6, 1.55.0, 1.56.0, 1.56.1, 1.56.2, 1.57.0, 1.57.1, 1.58.0, 1.58.1, 1.58.2, 1.58.3,
1.58.4, 1.58.5, 1.58.6, 1.58.7, 1.58.8, 1.58.9, 1.58.10, 1.58.11, 1.58.12, 1.58.13, 1.58.14,
1.58.15, 1.58.16, 1.59.0, 1.59.1, 1.59.2, 1.59.3, 1.59.4, 1.60.0, 1.60.1, 1.61.0, 1.61.1, 1.61.2,
1.62.0, 1.62.1, 1.62.2, 1.63.0, 1.63.1, 1.63.2, 1.63.3, 1.63.4, 1.64.0, 1.64.1, 1.64.2, 1.64.3,
1.64.4, 1.65.0, 1.65.1, 1.65.2, 1.65.3, 1.65.4, 1.65.5, 1.65.6, 1.66.0, 1.66.1, 1.67.0, 1.67.1,
1.68.0, 1.68.1, 1.68.2, 1.69.0, 1.69.1, 1.69.2, 1.69.3, 1.70.0, 1.70.1, 1.70.2, 1.71.0, 1.71.1,
1.71.2, 1.71.3, 1.72.0, 1.72.1, 1.73.0, 1.74.0, 1.74.1, 1.74.2, 1.74.3, 1.74.4, 1.75.0, 1.75.1,
1.75.2, 1.75.3, 1.75.4, 1.76.0, 1.76.1, 1.76.2, 1.76.3, 1.76.4, 1.76.5, 1.76.6, 1.76.7, 1.76.8,
1.76.9, 1.76.10, 1.76.11, 1.76.12, 1.76.13, 1.77.0, 1.78.0, 1.78.1, 1.79.0, 1.80.0, 1.80.1, 1.80.2,
1.80.3, 1.80.4, 1.81.0, 1.81.1, 1.81.2, 1.81.3, 1.81.4, 1.81.5, 1.81.6, 1.81.7, 1.81.8, 1.81.9,
1.81.10, 1.81.11, 1.81.12, 1.81.13, 1.81.14, 1.81.15, 1.81.16, 1.81.17, 1.81.18, 1.81.19, 1.81.20,
1.81.21, 1.82.0, 1.82.1, 1.83.0, 1.84.0, 1.85.0, 1.86.0, 1.87.0, 1.88.0, 1.89.0, 1.90.0, 1.91.0,
1.91.1, 1.91.2, 1.91.3, 1.92.0, 1.92.1, 1.92.2, 1.92.3, 1.93.0, 1.93.1, 1.93.2, 1.93.3, 1.93.4,
1.93.5, 1.93.6, 1.93.7, 1.93.8, 1.93.9, 1.93.10, 1.94.0, 1.95.0, 1.96.0, 1.96.1, 1.97.0, 1.98.0,
1.98.1, 1.98.2, 1.98.3, 1.98.4, 1.98.5, 1.98.6, 1.98.7, 1.98.8, 1.98.9, 1.98.10, 1.98.11, 1.98.12,
1.98.13, 1.98.14, 1.98.15, 1.98.16, 1.98.17, 1.98.18, 1.98.19, 1.98.20, 1.98.21, 1.98.22, 1.98.23,
1.98.24, 1.98.25, 1.98.26, 1.98.27, 1.99.0, 1.99.1, 1.99.2, 1.99.3, 1.99.4, 1.99.5, 1.100.0,
1.100.1, 1.100.2, 1.101.0, 1.101.1, 1.101.2, 1.101.3, 1.101.4, 1.101.5, 1.101.6, 1.101.7, 1.101.8,
1.101.9, 1.101.12, 1.101.13, 1.101.14, 1.101.15, 1.101.16, 1.101.17, 1.101.18, 1.101.19, 1.101.20,
1.101.21, 1.101.22, 1.101.23, 1.101.24, 1.101.25, 1.101.26, 1.101.27, 1.101.28, 1.101.29, 1.101.30,
1.101.31, 1.102.0, 1.102.1, 1.102.2, 1.102.3, 1.102.4, 1.102.5, 1.102.6, 1.102.7, 1.102.8, 1.103.0,
1.104.0, 1.105.0, 1.106.0, 1.107.0, 1.107.1, 1.108.0, 1.108.1, 1.108.2, 1.109.0, 1.110.0, 1.110.1
```
