---
name: tensor-grep-cross-platform-path-confinement
description: >-
  Use when writing or reviewing any code that confines filesystem access to a root (checkpoint
  snapshot/undo, LSP documentChanges, index reuse, install/upgrade, scan-writer paths) and must
  behave CORRECTLY on BOTH Windows and POSIX — especially junctions vs symlinks vs hardlinks, the
  drive-absolute `/C:/...` escape that only exists on Windows, canonicalize-or-fail-closed,
  handle-anchored identity versus resolve-then-act (TOCTOU/A38/A48), or a path-shape transform that
  is platform-meaningful (A84). Triggers: "junction", "symlink", "drive-absolute", "reparse point",
  "path confinement", "within_root", "TOCTOU", "canonicalize", "opened identity", "CWE-59",
  "CWE-1386", "mklink /J", "out-of-root". Sibling of tensor-grep-hermetic-hostile-tests (the fixture
  that must BITE) and tensor-grep-index-fingerprint-freshness (identity of the tree being served);
  this one is the cross-platform path-security discipline itself.
---

# tensor-grep: cross-platform path confinement

Path confinement is the class of defect this repo has had to fix repeatedly, and every one of the
fixes diverges between Windows and POSIX because the filesystem *primitives* diverge. A confinement
check that is correct on Linux is frequently a *no-op* — or an active hole — on Windows, and an
unconditional Windows transform re-creates the escape on POSIX (A84, #983). The discipline is to
name the platform primitive explicitly, gate every shape transform on the platform, and prefer
opened-identity anchors over resolve-then-act.

---

## Part 1 — The platform primitive table (know what you are actually protecting against)

| Primitive | Windows | POSIX | Confinement-relevant property |
|---|---|---|---|
| **Symlink** | needs privilege (or Developer Mode); created by `os.symlink` | ordinary `ln -s` | `Path.is_symlink()` is True; leaf refusals see it |
| **Junction** (directory reparse point) | `mklink /J`, `New-Item -ItemType Junction` — **NO privilege**; `os.walk` descends it as a plain dir; `Path.is_symlink()` is **False** | does not exist | **The Windows attack primitive.** A junctioned ANCESTOR under root is traversed transparently and reads OUT-of-root content — invisible to a leaf-symlink check | **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**
| **Hardlink** | `mklink /H` (same-volume only) | `ln` | No separate identity; both names resolve to the same inode — a within-root hardlink to an out-of-root inode is not a "link escape" but it IS the same content |
| **Reparse point / mount point** | volume mount points, file-physical reparse | bind mounts | `followlinks=False` on a walk does not stop a preceding ANCESTOR reparse |

**The M1 receipt (what the table is for):** the create-side checkpoint copy loop refused only LEAF
symlinks (`follow_symlinks=False`) — a junctioned/symlinked ANCESTOR under root copied
OUT-of-root content into the snapshot. The fix was parent-chain-only resolve + containment,
preserving the leaf's RAW identity (a legitimately tracked out-of-root-pointing leaf symlink is
stored AS a link, never refused and never followed). See `checkpoint_store.py` (grep
`def _resolve_parent_within_root` for the create-side parent-chain resolve; the earlier text cited
`_resolve_within_root` `:149` here, but that is the full-leaf UNDO-side resolver — the create side
is `_resolve_parent_within_root`, was `:167`, verified `:167` at this SHA; grep the symbol, never a
stamp) and `docs/plans/2026-08-08-backlog-completion-plan.md` M1 section.

---

## Part 2 — The drive-absolute escape (A84, #983) and platform-gated transforms

The `lsp_server.py` `_uri_to_path` drive-absolute strip: a Windows-only normalization that ran
UNCONDITIONALLY stripped the leading `/` from `/C:/Windows/evil` — on POSIX that root-anchored URI
became a RELATIVE `C:/Windows/evil` that resolved INSIDE the process cwd, flipping a confinement
check from refused→passed. **The rule (A84): any path-shape transformation that is
platform-meaningful must be gated on `os.name == "nt"` (or its POSIX analogue) AND both arms pinned
in a cross-platform test** — the real CI matrix is the only oracle that catches the flip; a
Windows-local green proves nothing about the POSIX arm.

Checked list for a path-shape transform:

- [ ] Is the transform meaningful only on one platform? Gate it on that platform explicitly.
- [ ] Are BOTH arms pinned in a cross-platform test (the transform applied vs not applied), so CI's
      OS matrix exercises each?
- [ ] Does the transform preserve the escape-class it is meant to close on the OTHER platform?
      (An unconditional strip re-created the escape — ask what the untransformed value resolves to
      on the sibling OS.)

---

## Part 3 — Canonicalize-or-fail-closed vs opened-identity anchoring (A38/A48/A53)

Two security contracts, both violated by the naive `path.resolve()`:

1. **Leaf-vs-parent (A38):** calling `.resolve()`/`realpath()` before a no-follow writer ERASES
   leaf-symlink identity. Even with a safe leaf check, an attacker can swap a PARENT or junction
   between the check and the mkdir/publication (TOCTOU). **Preserve the raw leaf identity; anchor
   directory creation, temp creation, and publication to opened identity-verified parent handles.**
2. **Handle anchoring (A48):** a leaf no-follow flag does not stop an intermediate PARENT swap.
   Create/open a stable fence, read/publish its protected index relative to the verified confined
   handle, and Event-test swaps before create, after lock, and before publish.

**Honest state at this SHA (grep, don't trust this paragraph):** the shared writer
`atomic_write_bytes` DELEGATES to `atomic_write_bytes_anchored` (`src/tensor_grep/cli/_index_lock.py`,
grep `def atomic_write_bytes`), whose parent `dir_fd` is opened POST-publish purely for directory-entry
fsync durability — it is an FSYNC anchor, NOT an identity-verified parent handle per A38. Receipt /
key / manifest writes through it therefore carry the leaf `is_symlink()` precheck plus
`O_EXCL | O_NOFOLLOW` on the temp create, but NO A38 parent-identity anchoring. The
opened-parent-handle version (A48) is explicitly DEFERRED, not claimed:
`docs/plans/2026-08-08-backlog-completion-plan.md` (grep `opened-parent-handle`) records it as a
NAMED follow-up, tracked in-tree as M1-FU1 `CHECKPOINT-A48-HANDLES` (grep that token in
`checkpoint_store.py`). The Event-gated parent-swap test stays RED-by-design until that lands —
as of this SHA it is UNWRITTEN (`rg "A48|opened.parent" tests/` returns nothing), so "RED-by-design"
here means "the A48 contract is unmet and has no test yet", not "an xfail exists".

**Canonicalize-or-fail-closed (A53's "name enforceable primitives"):** "atomic CAS" and "trusted
path" are goals, not Windows contracts. Name the concrete API and failure behavior; where the
platform primitive is unavailable, FAIL CLOSED instead of inventing a weaker fallback. The M17
index-reuse fix is the model: `canonical_root_of()` persisted (format v6) + canonical-relative
entry storage with a single deref (`canonical_root.join(rel)`) so NOTHING is cwd-dependent, and
`root_servability_reason` compares canonical query vs stored at BOTH load sites before serving —
never a bare `root == root` string compare.

**Confinement bots to internalize (CWE-59, CWE-1386, CERT FIO02-C):** CWE-59 "Improper Link
Resolution Before File Access" (TOCTOU on symlinks), CWE-1386 "Improper Handling of Syntactic
Inconsistency" (the drive-absolute form), FIO02-C "canonicalize path names before validating" —
but canonicalization alone is not enough: canonicalize for an IDENTITY claim, then compare the
OPENED object's identity (or canonical-relative projection), never re-resolve the path string.

---

## Part 4 — The M17 wrong-tree reuse failure as a confinement test (the REPRO shape)

M17 (index reuse) is a cross-platform confinement bug in disguise: `staleness_reason` checks only
no-ignore mode + per-entry mtime/size + new-file walk **over `self.root`** — it never asks "is this
still the queried tree". The wrong-tree serve needs an ALIAS: same string, swapped tree (a root
symlink/junction flipped after build, a renamed tree, or a copied/tampered `.tg_index`) — on
Windows the alias is a **junction**, exactly the primitive of Part 1.

- [ ] RED fixture = the ALIAS/SWAP shape (a junction/symlink under a fixed path swapped to a
      different dir between build and query; on Windows a junction), with the fixture-BITES
      precheck (see tensor-grep-hermetic-hostile-tests) so the swap is proven to have applied.
- [ ] The naive RED is UNREACHABLE when `.tg_index` lives INSIDE the tree (`resolve_index_path`) —
      a plain "build root A, query root B" never shares an index file. The REAL wrong-tree serve
      needs the swap topology, or the test proves nothing.
- [ ] The fix is canonical-root comparison at BOTH load sites (warm-declines / rebuild-on-mismatch
      BEFORE staleness), plus the canonical-relative entry projection of Part 3.

---

## Part 5 — Windows idioms and the fixture that must bite

- Junctions are created UNPRIVILEGED — Windows symlink `pytest.skip` rules do NOT apply to
  junction fixtures; `mklink /J` requires the LINK path must not already exist (the target
  directory MAY be populated) (A88),
  so the fixture must carry a BITE precheck proving the redirect actually resolves (`os.path.islink()`
  is False on a junction — assert the negative shape; the parent-resolve containment is the guard
  that matters). **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**
- `wsl.exe` / WSL paths are a separate domain; the WSL path-domain bridge (path-domain translation,
  `wslpath`/`wslpath -w`) is governed by its own contract — see the Task 2A/2B design language around
  `PathDomainContractV1` in `docs/plans/2026-08-02-backlog-closeout-design.md` (grep
  `PathDomainContract`) for the exhaustive-translation-reason set and the "consumed marker" invariant.
- A path-shape transform that is platform-meaningful (Part 2) MUST never run un-gated; the CI OS
  matrix is the oracle (A84/A85).


### Settling contested platform facts with a bounded probe (2026-08-13, A107)

When review seats assert opposite facts about a toolchain-version-dependent platform behavior,
do not re-vote — probe. A tiny std-only `cargo run --release` program (positive + negative
controls: known-symlink and known-regular fixtures) on the PINNED toolchain settled the
junction question for Rust 1.96.0 in ~30s and became the only artifact all seats cite
(probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5).
If the result supersedes a documented claim (e.g. A88's "junctions are NOT symlinks"), the
superseded claim itself carries an append-only SUPERSEDED note — never silently rewritten (A94).

---

## External anchors (Exa research, 2026-08-09)

| Anchor | Their point | This skill's mapping |
|---|---|---|
| **MSRC — "RedirectionGuard: Modeling Security-Sensitive Path Redirects"** (microsoft.com) | Introduces "RedirectionGuard" as a path-based access control discipline; security-sensitive path resolution must be modelled as a redirect possible at MANY points (junctions, symlinks, mount points) | Part 1's primitive table + Part 3's open-and-verify: no single api call closes all redirects; the parent chain IS the attack surface. |
| **Unit42 — "Windows Junctions, Symlinks and Security Trust Levels"** (Unit42/Palo Alto blog) | Details how junctions (no privilege) vs symlinks (privileged) occupy different trust levels for attackers | Part 1's junction-as-attack-primitive + the unprivileged-fixture rule in Part 5. |
| **ZDI — reparse-point / TOCTOU writeups** (ZDI-advisories) | Reparse-point race: an attacker swaps a reparse point between a security check and an open/write | Part 3's handle-anchoring (Event-gated parent swap) — the TOCTOU the resolve-then-act shape cannot close. |
| **CWE-59 / CWE-1386 / CERT FIO02-C** | Link-resolution-before-file-access TOCTOU; syntactic-inconsistency drives the escape; canonicalize before validating | Part 3's canonicalize-for-identity + fail-closed. |

**Repo receipts to cite by symbol, not line:** `checkpoint_store.py` `_resolve_within_root` + the
create-side parent-chain resolve (M1); `lsp_server.py` `_uri_to_path` / `_valid_external_document_uri`
/ `_workspace_edit_refused` (M3 documentChanges confinement); `index.rs` `staleness_reason` /
`canonical_root_of` / `root_servability_reason` (M17, merged on origin/main as v1.110.12; derive the
current tag with `git describe --tags --abbrev=0` — a stamped tag here rots on every release); AGENTS.md
A38/A48/A53/A55/A84. Grep the symbol, never a hardcoded line number.

---

## Quick reference

```
[1] primitives   name the platform primitive (junction != symlink != hardlink) you protect against **SUPERSEDED for the pinned Rust 1.96.0 toolchain: a real `mklink /J` junction reports `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` (bounded probe receipt: docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5); the CPython `os.path.islink()` half of the claim stays true.**
[2] transforms   gate platform-meaningful shape transforms; pin BOTH arms cross-platform
[3] identity     preserve raw leaf; anchor to opened verified parent handles (not resolve-then-act)
[4] canonical    canonicalize for IDENTITY, then compare the opened/canonical-relative object
[5] REPRO        a wrong-tree serve needs an ALIAS/SWAP topology (junction), never the naive RED
[6] fixtures     junction fixtures need NO privilege but mklink /J requires LINK path absent (target MAY be populated) — BITE it
```

The endpoint: confinement that holds on BOTH platforms because the primitive is named, the transform
is gated, the identity is opened (not resolved), and the hostile fixture is proven hostile.
