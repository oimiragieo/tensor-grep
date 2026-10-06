# Checkpoint label validation error sanitization

CI 37433094569, head c82e9d9db9289027bc896cfafaa261beb535a3f6, Linux Python 3.12
job 112169486515 failed the existing narrow MCP exception-formatting ratchet after
6,697 passing tests. The new label-validation ValueError arm in mcp_audit_tools.py
echoes str(exc); ordinary validator messages are fixed today, but that does not justify
exposing an arbitrary ValueError or widening the security guard.

1. In the legacy checkpoint-create label-validation arm, remove the exception binding
   and return a literal message: "Checkpoint label must contain 1 to 120 printable characters."
   Keep invalid_input, refused path, version stamps and validation-before-writes unchanged.
   Do not change the PathConfinementError exemption or any other error arm. The meta create
   tool delegates to this handler, so the same correction applies to both public tools.
2. Add a deterministic parametrized regression for legacy and meta create. Inject a
   ValueError with a private-path/sentinel message from the label normalizer; assert the
   exact fixed invalid_input/refused-path response, no sentinel/path disclosure, no store
   invocation and complete fixture names/directories/file bytes unchanged. Include existing
   valid-label and invalid-label tests as positive controls. Do not loosen any ratchet.
3. Reproduce the exact existing failing node before the change under an external timeout;
   then run the full sanitization test file plus label/wire tests, focused lint/preview,
   full mypy/size/bare, actual handler assertions and labels replay in the canonical Windows
   venv. Re-anchor only out-of-span handler locations if required. Guard inventory identities
   and exception pins must remain unchanged. Check sibling label-validation paths explicitly.
4. Commit source and evidence separately. Obtain independent exact-head review and restored
   Opus security review; the provider-limit seat is FAILED. Actual dependency-squash
   integration, complete exact-head CI and published replay remain mandatory. Earlier SHIP
   verdicts do not clear this runtime amendment.
