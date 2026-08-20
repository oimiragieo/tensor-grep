# Rebuild guides

This directory exists to close a specific documentation gap: an audit found that no feature in
tensor-grep was documented well enough that **a junior-level analyst could rebuild it from
scratch** — no worked example existed, no verification checklist existed, and cache/schema
versioning behavior was undocumented. This directory and `docs/design/README.md` are the fix.

- **`tg-checkpoint.md`** — the worked template. Picks `tg checkpoint` (create/list/undo) as a
  self-contained, fully-tested feature and documents it end to end: the problem it solves, the
  data flow, every file's contribution, the on-disk format (verified by actually running it), the
  eight traps a naive reimplementation gets wrong (each tied to a real guard and a real test), and
  what is explicitly out of scope. **Future rebuild guides should follow this one's shape.**
- **`verification-checklist.md`** — the general "how do I prove a rebuild is correct" checklist:
  run the feature's own tests first (and confirm they were once RED, not just currently green),
  dogfood the real shipped binary rather than an internal function call, round-trip a stateful
  feature against a throwaway scratch directory and read the real on-disk artifact, walk a guide's
  claimed "traps" against the real guarding code, run the governance gates every change in this
  repo owes, and report verification in three explicit tiers (ran-and-observed / read-and-cited /
  unverified) rather than one undifferentiated "verified" claim.
- **`cache-and-schema-versioning.md`** — what cache/schema versioning actually exists in this
  codebase (there is no migration framework; one real schema-gated cache invalidation exists,
  cross-checked Rust/Python, backed by a real test; everything else stamps a version field without
  enforcing it on read). Written so the next engineer does not go looking for migration machinery
  that was never built.

See `docs/design/README.md` for the sibling convention covering *design* docs (the argument for
why a shape is correct, before it is built) — a rebuild guide instead documents a shape *after* it
exists, for someone reconstructing or extending it.
