# Plan — #304 / #305: whole-repo commands must degrade, never return empty

Status: DRAFT (awaiting thinktank review)
Author: backlog-steward session, 2026-07-26
Goal: #292 (trustworthy tg) / the CEO enterprise-readiness answer of 2026-07-26

Covers two symptoms with one root cause:

- **#304** — bare `tg agent REPO` (no explicit `--deadline`) times out at ~75s on WSL `/mnt/c` and
  returns **empty**, not a partial answer.
- **#305** — `tg codemap` times out on WSL; the enterprise skill currently tells operators to skip it.

## 0. STEP ZERO — re-verify before planning anything

Both symptoms were **last observed 2026-07-21 @ v1.91.0**. Since then, #233 (deadline-truncated agent
emits best-effort primary), #235 (codemap emits useful partial map under deadline), #153 (codemap
default wall-clock deadline), #220 and #222 all landed. Current line is v1.98.25.

**Do not build against a stale observation.** Reproduce both on the current published wheel first.

There is a specific reason to suspect the fix exists but does not reach this path: **#200 was exactly
this shape** — `tg agent --deadline` was silently ignored on the *default warm-daemon* path while the
cold-path backstop worked fine. If #233's best-effort primary is likewise cold-path-only, the bug is
"the fix doesn't reach the default route", not "there is no fix". That is a much smaller change and a
completely different plan.

Outcome of step zero decides everything below:
- **Reproduces** → build the plan.
- **Does not reproduce** → this is stale doc-drift; fix the enterprise skill rows and close.
- **Reproduces only on the default/warm route** → it is a #200-class routing gap, not an algorithm gap.

## 1. Root cause framing (the useful part)

The WSL `/mnt/c` slowness itself is **settled physics — do not re-prove it**. For the record, the
magnitude is real: directory traversal 343,338 files/s native vs 13,561 files/s crossing to Windows
(~25x); 1000 open/write/read/stat cycles 0.054s native vs 13.5s via `/mnt/c` (~250x). The mechanism
is the 9P protocol over a Hyper-V socket with 64KB-bounded messages, so **per-operation round-trip
overhead dominates** — which is why small-file-heavy source trees suffer worst. virtiofs exists but
is still opt-in; the default transport is unchanged.
([benchmark](https://github.com/webbertakken/wsl-filesystem-benchmark),
[WSL#9430](https://github.com/microsoft/WSL/issues/9430))

Crossing the boundary is bad in **both** directions — Windows→WSL via `\\wsl.localhost` averages ~14%
of native. So "just use the other path" is not a fix.

**The actionable insight is not about WSL at all.** It is this, from the anytime-algorithm literature
(Dean & Boddy 1988; Russell & Zilberstein 1991):

> An **interruptible** algorithm produces a usable result at *any* interruption point, with quality
> improving monotonically. A **contract** algorithm only guarantees a result if run to its
> pre-declared time, and is *"useless"* if cut short.

A command that returns **nothing** on timeout is, by this taxonomy, **a contract algorithm being
interrupted before its contract time** — i.e. it is accidentally implemented as the one class that is
*allowed* to return garbage. WSL merely makes the timeout fire often enough to notice.

That reframes the fix: not "make it faster on WSL", but **"make it interruptible."**

## 2. The steal-list, ranked

**S1 — Make the deadline path return the best partial, never empty.** *(fixes #304 directly)*
Effort: small-medium. No new infrastructure. Restructure the walk/scan loop to (a) process in
priority order — most-central files, or nearest-cwd, first; (b) check the deadline periodically;
(c) on expiry return whatever accumulated, with an explicit incompleteness marker. This is exactly
Elasticsearch's `timeout` + `allow_partial_search_results` contract, and it is the same in-band
honesty contract as #276 — **the two items share a vocabulary and should share it deliberately**
(`result_incomplete` + `incomplete_reason_class = "deadline"`).

Where a genuinely multi-pass computation is involved, the literature gives a free upgrade: any
contract algorithm converts to interruptible by **iterative doubling** (run at 1, 2, 4, 8… time
units, keep the last completed pass), with a proven optimal acceleration-ratio bound of exactly 4.

**S2 — Detect a slow cross-OS mount in <1ms and lower ambition automatically.** *(fixes the WSL root
cause proactively for both)* Effort: small. `statfs(path)` → `f_type == 0x01021997` (`V9FS_MAGIC`)
identifies a 9p mount in microseconds; it is answered by the mount table, not the remote side, so it
is cheap *even on a slow mount*. util-linux's own `mnt_fstype_is_netfs()` already classes `9p`
alongside cifs/nfs/smb as "expect this to be slow", so this is a recognized signal, not a heuristic.
On detection tg can pre-emptively lower its file ceiling / tighten its default deadline / emit one
honest line suggesting a native-filesystem clone — rather than discovering the slowness empirically
*after* blowing the budget.
Corroborating (weaker) signals: `/proc/mounts` fstype, `WSL_DISTRO_NAME`, `/proc/version` containing
"microsoft", `/mnt/[a-z]/` prefix. `statfs` `f_type` is the authoritative one.

**S3 — Prefer `git ls-files` over a raw walk when the target is a git repo.** *(fixes #305 cost, and
#304's walk cost generally)* Effort: small. Measured **5x+ faster than `fd`/`find`** even on native
filesystems, because it reads git's index directly with **zero per-file stat calls**. On a 9p mount
the win should be disproportionately larger, since the saving is proportional to *syscalls
eliminated* and each syscall carries a protocol round-trip. Compose it as a fast path in front of the
existing `ignore`-crate walker, falling back for untracked files (`git ls-files -o`) and non-git trees.
**This must not change results** — it changes *how* files are enumerated, so it needs a
files(A) == files(B) differential check, the same shape as the #270 regression guard.

**S4 — Persistent incrementally-updated index.** Effort: **large**. The Watchman / zoekt / Bazel
pattern. Deliberately **deferred**: `.tg_index` was already measured **net-negative (~10x slower)**
here, and this repo's own history says the returns were bugs, not milliseconds. Do not reopen without
a measurement mandate.

**S5 — Monorepo-style scope inference** (Nx/Turborepo `affected`: git-diff → dependency graph →
changed + dependents). Effort: medium. A principled way to bound scope rather than an arbitrary
file-count cutoff. Reasonable follow-up after S1–S3, not before.

## 3. What NOT to build

| Do not build | Why |
| --- | --- |
| Directory-mtime rollup cache to skip subtrees | **Unsound.** A directory's mtime updates when child *names* change, not when a file's *content* is edited in place — on ext4/NTFS/APFS/btrfs alike. It would silently miss the single most common developer action: saving a file. |
| mtime-only invalidation | Breaks on sub-ms git-checkout writes, watcher-queue overflow, and mtime *reversion* on branch switch (an older commit can set an mtime older than the last-scan watermark, hiding the change). mtime is a pre-filter; content hash is the truth. |
| Anything that "fixes" WSL's 9p from tg's side | Microsoft's problem; virtiofs is still opt-in. Detect and adapt, don't route around the hypervisor. |
| GVFS/ProjFS-style filesystem virtualization | Microsoft's own team walked this back for Scalar in favour of sparse-checkout + declared dependencies. Last resort, not a starting point. |
| A hard wall-clock timeout as the *only* bound | tg already learned this once (#52: "each stage bounded" ≠ pipeline bounded). A deadline without interruptible internal structure just moves where the failure happens. |

## 4. Verification

- **Step zero is itself a bidirectional check**: the symptom must reproduce on the current wheel. If
  it does not, the plan is void and the doc is the bug.
- **S1**: control arm = the same command under a deadline it comfortably meets → complete result, no
  marker, exit 0. Treatment = a deadline it cannot meet → **non-empty partial**, `result_incomplete`,
  `incomplete_reason_class = "deadline"`, exit 2. A test that only asserts the timeout case cannot
  distinguish "returns partial" from "returns nothing but sets a flag" — assert the payload is
  non-empty.
- **S2**: the detector must be tested on a **non-9p** path too and return false. A detector that
  returns true everywhere is the "passes in both arms" failure.
- **S3**: differential — the file set from `git ls-files` must equal the file set from the walker on
  the same tree, including the untracked and non-git fallbacks. This is the one that can silently
  change results.
- **Dogfood on a real large repo over a real `/mnt/c` mount** before believing any of it. Fixture-green
  is false for anything performance- or filesystem-shaped.

## 5. Open questions for thinktank

1. Is #304 an algorithm gap or a #200-class routing gap? (Step zero answers this; the plan branches
   hard on it.)
2. Should S2 *silently* lower ambition, or say so? Silently adapting makes results depend on
   invisible environment state — arguably a trust violation in a tool whose whole goal is honesty.
   Proposal: adapt **and** disclose in the envelope.
3. Does S3's `git ls-files` fast path respect tg's ignore semantics identically? If not, it changes
   the file set — the exact defect class as #264/#267.
4. Is iterative doubling worth it for `tg agent`, or does the priority-ordered single pass of S1
   already capture the value?
