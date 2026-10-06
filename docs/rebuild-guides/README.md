# Rebuild guides

These guides explain implementation structure, state formats, and verification
steps for contributors reconstructing or extending a feature.

- [Checkpoints](tg-checkpoint.md): create, list, and undo file snapshots, including
  containment, atomic restore, and disk-budget constraints.
- [Ledger](tg-ledger.md): advisory claims and content-addressed findings, repository
  scoping, conflict reporting, and blob verification.
- [Cache and schema versioning](cache-and-schema-versioning.md): cache invalidation
  and compatibility responsibilities across Python and Rust.
- [Verification checklist](verification-checklist.md): exercise public entry points,
  inspect persisted state, and distinguish observed behavior from source review.

Examples illustrate data shapes. Re-run them in a disposable directory against
the version being changed; do not treat an old example as current release evidence.
