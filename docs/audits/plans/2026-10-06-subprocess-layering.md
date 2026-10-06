# Subprocess decoder layering correction

CI run `37429574908` on `574d18bf47774148a016ace7835314bc3e8f54e9` found two
new forbidden imports: `backends.ast_wrapper_backend` and `backends.rust_backend`
import decoder helpers from `cli.subprocess_policy`. The existing import-graph
burn-down must remain unchanged. This is a production layering correction.

1. Move the three pure helpers `decode_protocol_output`, `decode_diagnostic_output`
   and `decode_path_record` into `core/subprocess_decoding.py`. Preserve their
   signatures, docstrings and function ASTs exactly. The new module depends only
   on the standard library. Do not change `core.__init__` exports.
2. Import the core helpers directly in both affected backends. Preserve the three
   public CLI helper names as explicit re-exports from `cli.subprocess_policy`;
   its timeout configuration and subprocess wrapper stay in place. All existing
   CLI consumers and monkeypatch seams remain compatible.
3. Pin identity of all three CLI exports with their core functions. Existing
   byte/protocol/path tests continue to prove behavior. Use the unmodified full
   import-edge tests, including their mutation controls, as the architecture gate.
4. Regenerate the subprocess inventory and verify that only source line numbers
   change. The 72 sinks, 16 text-decoding calls, 12 generated calls, seven exact
   exceptions, fingerprints, policies, options and rationales must remain intact.
   No import baseline, exception or size budget may grow. Reanchor a handler
   ledger location only if it moved outside its actual function span.
5. Verify in the canonical Windows venv: import edges, decoder/AST/Rust consumers,
   guard controls and cross-interpreter inventory, full Ruff/preview/mypy, size,
   bare-call and relevant handler gates, plus source decoding replay. CI retains
   the full native and platform matrices. Review the exact resulting artifact
   independently and through Opus because backend/security-class files change.
6. Merge this bounded correction into the combined labels candidate, prove its
   exact source delta is the same correction, and refresh affected verification,
   reviews and CI. The release ordering stays subprocess/ranking first, followed
   by labels against the actual squash commits. Superseded failing CI gives no
   clearance; any later failure must be classified from its own evidence.

Acceptance requires preserved decoder behavior and public CLI import compatibility,
zero new layering violations, and no widened ratchet. The review hash covers the
canonical worktree bytes of this plan, before implementation.
