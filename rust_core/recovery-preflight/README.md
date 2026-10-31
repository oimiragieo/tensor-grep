The preflight package builds the shared source with a smaller dependency graph.
When updating a shared dependency, update both Cargo manifests as needed and
regenerate both lockfiles in the same change. Update the expected direct pins
in `tests/unit/test_recovery_preflight_package.py`, then run that test and the
preflight package's Rust tests. The Python test requires the standalone lock's
registry packages to match the reachable root lock dependencies. The nested
Dependabot entry groups updates and limits routine PRs to one. Update both
locks together when a bot proposes a change to a shared dependency. The audit
workflow checks both lockfiles.
